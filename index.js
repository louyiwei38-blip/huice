'use strict';

require('dotenv').config();

const cron = require('node-cron');

const { fetchCandles }             = require('./src/data');
const { checkSignal, SR_TOLERANCE, TRENDLINE_TOLERANCE } = require('./src/signal');
const { sendSignal, sendDailyReport } = require('./src/notify');
const { getConfidence, computeWinRates } = require('./src/confidence');

const SYMBOL   = 'BTC/USDT:USDT';  // Binance USDM perpetual
const LOOKBACK = 1000;

// Last processed candle timestamp per timeframe (avoids duplicate signals)
const lastCandleTs = { '4h': 0, '1h': 0 };

// Pending exits: entry trades waiting for 1H hold to complete
const pendingExits = [];

// Completed trades today (for daily report)
const todayTrades  = [];

// ─── Signal Checker ───────────────────────────────────────────────────────────

async function checkTimeframe(tf) {
  const candles = await fetchCandles(SYMBOL, tf, LOOKBACK);
  if (!candles.length) return;

  const lastTs = candles[candles.length - 1].timestamp;
  if (lastTs === lastCandleTs[tf]) return; // Same candle already processed
  lastCandleTs[tf] = lastTs;

  const signal = checkSignal(candles, tf);
  if (!signal) return;

  const timeStr = signal.time.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  console.log(`[${tf.toUpperCase()}] ${signal.direction} @ ${signal.price.toFixed(2)} | ${signal.reason} | ${timeStr}`);

  pendingExits.push({
    ...signal,
    entryPrice : signal.price,
    exitAt     : new Date(signal.time.getTime() + 3600 * 1000),
  });

  const confidence = await getConfidence(signal).catch(() => null);
  await sendSignal(signal, confidence);
}

// ─── Exit Tracker ─────────────────────────────────────────────────────────────

async function processPendingExits() {
  const now  = Date.now();
  const done = pendingExits.filter(t => t.exitAt.getTime() <= now);
  if (!done.length) return;

  // Fetch current price once for all exits
  const candles    = await fetchCandles(SYMBOL, '1h', 2);
  const exitPrice  = candles[candles.length - 1].close;

  for (const trade of done) {
    pendingExits.splice(pendingExits.indexOf(trade), 1);

    const pnl  = trade.direction === 'LONG' ? exitPrice - trade.entryPrice : trade.entryPrice - exitPrice;
    const win  = pnl > 0;
    const mark = win ? '✅' : '❌';

    todayTrades.push({ ...trade, exitPrice, pnl, win });

    console.log(
      `[出场] ${mark} ${trade.direction} ${trade.timeframe.toUpperCase()} | ` +
      `入: ${trade.entryPrice.toFixed(2)} → 出: ${exitPrice.toFixed(2)} | ` +
      `PnL: ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT`
    );
  }
}

// ─── Daily Report ─────────────────────────────────────────────────────────────

async function sendDailyReportNow() {
  const wins    = todayTrades.filter(t => t.win).length;
  const total   = todayTrades.length;
  const winRate = total > 0 ? wins / total : 0;
  const totalPnl = todayTrades.reduce((s, t) => s + (t.pnl || 0), 0);

  await sendDailyReport({
    winRate,
    total,
    wins,
    losses     : total - wins,
    pnlSummary : `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} USDT`,
  });

  todayTrades.length = 0;
  console.log('[日报] 已推送，交易记录已重置');
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log('=== BTCUSDT 震荡反转信号系统 ===');
  console.log(`参数: SR容差=${(SR_TOLERANCE * 100).toFixed(2)}% | 趋势线容差=${(TRENDLINE_TOLERANCE * 100).toFixed(2)}% | 回溯=${LOOKBACK}根`);
  console.log('轮询间隔: 每分钟\n');

  const poll = async () => {
    await checkTimeframe('4h').catch(e => console.error('[4H 错误]', e.message));
    await checkTimeframe('1h').catch(e => console.error('[1H 错误]', e.message));
    await processPendingExits().catch(e => console.error('[出场 错误]', e.message));
  };

  // Pre-compute confidence cache in background (non-blocking)
  computeWinRates().catch(e => console.error('[置信度]', e.message));

  // Initial check
  await poll();

  // Poll every minute
  cron.schedule('* * * * *', poll);

  // Daily report at 22:00 Beijing (UTC+8) = 14:00 UTC
  cron.schedule('0 14 * * *', () => {
    sendDailyReportNow().catch(e => console.error('[日报 错误]', e.message));
  });

  console.log('系统运行中... (Ctrl+C 退出)');
}

main().catch(err => {
  console.error('启动失败:', err);
  process.exit(1);
});
