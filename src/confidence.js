'use strict';

const { fetchCandles } = require('./data');
const { checkSignal }  = require('./signal');

const SYMBOL       = 'BTC/USDT:USDT';  // Binance USDM perpetual
const LOOKBACK     = 1000;
const HISTORY_DAYS = 50;          // days used to compute win rates
const CACHE_TTL    = 4 * 3600 * 1000; // refresh every 4 hours
const MIN_SAMPLES  = 5;           // minimum trades needed to show confidence

const TF1H_MS = 3600 * 1000;
const TF4H_MS = 4 * 3600 * 1000;

// In-memory cache: category → { winRate, total }
let cache     = {};
let computedAt = 0;

// ─── Category ────────────────────────────────────────────────────────────────

/**
 * Derive a category key from a signal for bucketing historical trades.
 * Format: "<timeframe>_<direction>_<triggerType>"
 */
function getCategory(signal) {
  let trigger = 'other';
  if (signal.reason.includes('支撑位'))     trigger = 'support';
  else if (signal.reason.includes('阻力位')) trigger = 'resistance';
  else if (signal.reason.includes('上升趋势线')) trigger = 'uptrend';
  else if (signal.reason.includes('下降趋势线')) trigger = 'downtrend';
  return `${signal.timeframe}_${signal.direction}_${trigger}`;
}

// ─── Compute ──────────────────────────────────────────────────────────────────

async function computeWinRates() {
  console.log(`[置信度] 计算近 ${HISTORY_DAYS} 天历史胜率...`);

  const candles1H = await fetchCandles(SYMBOL, '1h', HISTORY_DAYS * 24 + LOOKBACK + 10);
  const candles4H = await fetchCandles(SYMBOL, '4h', HISTORY_DAYS * 6  + LOOKBACK + 10);

  const testStart = candles1H.length - HISTORY_DAYS * 24;
  const tally = {}; // category → { wins, total }

  const record = (signal, entryClose, exitClose) => {
    const cat = getCategory(signal);
    if (!tally[cat]) tally[cat] = { wins: 0, total: 0 };
    const pnl = signal.direction === 'LONG' ? exitClose - entryClose : entryClose - exitClose;
    tally[cat].total++;
    if (pnl > 0) tally[cat].wins++;
  };

  for (let i = testStart; i < candles1H.length - 1; i++) {
    const curTs = candles1H[i].timestamp;

    // 1H signal
    const w1H  = candles1H.slice(Math.max(0, i - LOOKBACK + 1), i + 1);
    const sig1H = checkSignal(w1H, '1h');
    if (sig1H) {
      record(sig1H, candles1H[i].close, candles1H[i + 1].close);
      continue;
    }

    // 4H signal (only at 4H close)
    if ((curTs + TF1H_MS) % TF4H_MS === 0) {
      const idx4H = candles4H.findIndex(c => c.timestamp === curTs - 3 * TF1H_MS);
      if (idx4H > 0) {
        const w4H   = candles4H.slice(Math.max(0, idx4H - LOOKBACK + 1), idx4H + 1);
        const sig4H = checkSignal(w4H, '4h');
        if (sig4H) {
          record(sig4H, candles1H[i].close, candles1H[i + 1].close);
        }
      }
    }
  }

  // Build cache
  const newCache = {};
  for (const [cat, { wins, total }] of Object.entries(tally)) {
    newCache[cat] = { winRate: total > 0 ? wins / total : 0, total };
  }
  cache      = newCache;
  computedAt = Date.now();

  console.log('[置信度] 计算完成:');
  for (const [cat, { winRate, total }] of Object.entries(newCache)) {
    console.log(`  ${cat}: ${(winRate * 100).toFixed(1)}% (${total}次)`);
  }
}

// ─── Query ────────────────────────────────────────────────────────────────────

/**
 * Get confidence info for a signal.
 * Returns null if cache is empty or sample size is too small.
 */
async function getConfidence(signal) {
  if (Date.now() - computedAt > CACHE_TTL || Object.keys(cache).length === 0) {
    await computeWinRates().catch(e => console.error('[置信度] 刷新失败:', e.message));
  }

  const data = cache[getCategory(signal)];
  if (!data || data.total < MIN_SAMPLES) return null;

  const wr    = data.winRate;
  const level = wr >= 0.70 ? '高' : wr >= 0.55 ? '中' : '低';
  const stars = wr >= 0.70 ? '⭐⭐⭐' : wr >= 0.55 ? '⭐⭐' : '⭐';

  return { winRate: wr, total: data.total, level, stars };
}

module.exports = { getConfidence, computeWinRates, getCategory };
