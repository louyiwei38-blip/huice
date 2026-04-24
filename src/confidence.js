'use strict';

const { fetchCandles } = require('./data');
const { checkSignal }  = require('./signal');
const { isRanging }    = require('./indicators');

const SYMBOL       = 'BTC/USDT:USDT';  // Binance USDM perpetual
const LOOKBACK     = 1000;
const HISTORY_DAYS = 50;          // days used to compute win rates
const CACHE_TTL    = 4 * 3600 * 1000; // refresh every 4 hours
const MIN_SAMPLES  = 5;           // minimum trades needed to show confidence

const TF1H_MS = 3600 * 1000;

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

  const TF15M_MS = 15 * 60 * 1000;
  const candles1H  = await fetchCandles(SYMBOL, '1h',  HISTORY_DAYS * 24  + LOOKBACK + 10);
  const candles15M = await fetchCandles(SYMBOL, '15m', HISTORY_DAYS * 96  + LOOKBACK + 10);

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

    // ADX ranging filter: only process signals in ranging conditions
    const adxWindow = candles1H.slice(Math.max(0, i - 99), i + 1);
    if (!isRanging(adxWindow)) continue;

    // 1H signal
    const w1H  = candles1H.slice(Math.max(0, i - LOOKBACK + 1), i + 1);
    const sig1H = checkSignal(w1H, '1h');
    if (sig1H) {
      record(sig1H, candles1H[i].close, candles1H[i + 1].close);
      continue;
    }

    // 15M signal (check all 15M candles within this 1H candle period)
    const h1Open = curTs - TF1H_MS;
    const idx15Start = candles15M.findIndex(c => c.timestamp >= h1Open);
    if (idx15Start >= 0) {
      for (let k = idx15Start; k < candles15M.length && candles15M[k].timestamp < curTs; k++) {
        const w15M  = candles15M.slice(Math.max(0, k - LOOKBACK + 1), k + 1);
        const sig15 = checkSignal(w15M, '15m');
        if (sig15) {
          record(sig15, candles1H[i].close, candles1H[i + 1].close);
          break;
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
