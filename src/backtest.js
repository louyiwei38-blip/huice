'use strict';

const { fetchCandles } = require('./data');
const { checkSignal }  = require('./signal');

const SYMBOL   = 'BTC/USDT';
const DAYS     = 100;
const LOOKBACK = 1000;

const TF4H_MS = 4 * 3600 * 1000;
const TF1H_MS = 3600 * 1000;

/**
 * Run 100-day backtest.
 * - Entry: close of signal candle
 * - Exit:  close of next 1H candle (fixed 1-hour hold)
 * - Win:   LONG if exit > entry, SHORT if exit < entry
 */
async function runBacktest() {
  console.log('\n=== BTCUSDT 震荡反转策略回测 ===');
  console.log(`回测周期: 最近 ${DAYS} 天`);
  console.log(`SR容差: 0.3% | 趋势线容差: 0.03% | 回溯K线: ${LOOKBACK}\n`);
  console.log('数据获取中...');

  // 1H: test window (DAYS*24) + lookback buffer
  const limit1H   = DAYS * 24 + LOOKBACK + 10;
  const candles1H = await fetchCandles(SYMBOL, '1h', limit1H);

  // 4H: test window (DAYS*6) + lookback buffer
  const limit4H   = DAYS * 6 + LOOKBACK + 10;
  const candles4H = await fetchCandles(SYMBOL, '4h', limit4H);

  console.log(`1H 数据: ${candles1H.length} 根`);
  console.log(`4H 数据: ${candles4H.length} 根\n`);

  // Index in candles1H where the 100-day test window starts
  const testStart = candles1H.length - DAYS * 24;
  if (testStart < LOOKBACK) {
    console.error('数据不足，无法回测');
    return;
  }

  const trades = [];

  for (let i = testStart; i < candles1H.length - 1; i++) {
    const curTs = candles1H[i].timestamp;

    // ── 1H signal ───────────────────────────────────────────────────────────
    const w1H     = candles1H.slice(Math.max(0, i - LOOKBACK + 1), i + 1);
    const sig1H   = checkSignal(w1H, '1h');

    if (sig1H) {
      trades.push(buildTrade(sig1H, candles1H[i], candles1H[i + 1]));
      continue; // Only one trade per bar
    }

    // ── 4H signal (only at 4H candle closes) ────────────────────────────────
    // A 4H candle closes when the NEXT 1H bar would be a new 4H open.
    // i.e. (curTs + TF1H_MS) is divisible by TF4H_MS
    if ((curTs + TF1H_MS) % TF4H_MS === 0) {
      const fourHOpenTs = curTs - 3 * TF1H_MS;
      const idx4H = candles4H.findIndex(c => c.timestamp === fourHOpenTs);
      if (idx4H > 0) {
        const w4H   = candles4H.slice(Math.max(0, idx4H - LOOKBACK + 1), idx4H + 1);
        const sig4H = checkSignal(w4H, '4h');
        if (sig4H) {
          trades.push(buildTrade(sig4H, candles1H[i], candles1H[i + 1]));
        }
      }
    }
  }

  // ── Statistics ─────────────────────────────────────────────────────────────
  const wins    = trades.filter(t => t.win).length;
  const losses  = trades.length - wins;
  const winRate = trades.length > 0 ? wins / trades.length : 0;
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
  const avgWin   = wins   > 0 ? trades.filter(t =>  t.win).reduce((s, t) => s + t.pnl, 0) / wins   : 0;
  const avgLoss  = losses > 0 ? trades.filter(t => !t.win).reduce((s, t) => s + t.pnl, 0) / losses : 0;

  console.log('=== 回测结果 ===');
  console.log(`总交易次数 : ${trades.length}`);
  console.log(`胜率       : ${(winRate * 100).toFixed(2)}%`);
  console.log(`盈利次数   : ${wins}`);
  console.log(`亏损次数   : ${losses}`);
  console.log(`总盈亏     : ${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} USDT`);
  console.log(`平均盈利   : +${avgWin.toFixed(2)} USDT`);
  console.log(`平均亏损   : ${avgLoss.toFixed(2)} USDT`);

  return { trades, wins, losses, total: trades.length, winRate, totalPnl };
}

function buildTrade(signal, entryCandle, exitCandle) {
  const entry = entryCandle.close;
  const exit  = exitCandle.close;
  const pnl   = signal.direction === 'LONG' ? exit - entry : entry - exit;
  return {
    direction : signal.direction,
    timeframe : signal.timeframe,
    reason    : signal.reason,
    entryTime : entryCandle.time,
    exitTime  : exitCandle.time,
    entry,
    exit,
    pnl,
    win: pnl > 0,
  };
}

module.exports = { runBacktest };
