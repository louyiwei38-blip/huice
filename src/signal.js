'use strict';

const { getSupportResistance, getTrendlines } = require('./indicators');

const SR_TOLERANCE        = 0.0005;  // 0.05% — support/resistance core zone
const TRENDLINE_TOLERANCE = 0.0003;  // 0.03% — trendline touch

/**
 * Check whether the last closed candle in `candles` triggers a signal.
 *
 * Priority order:
 *   1. Support zone   → LONG
 *   2. Uptrend line   → LONG
 *   3. Resistance zone → SHORT
 *   4. Downtrend line  → SHORT
 *
 * @param {Array}  candles   - ordered array of candle objects (last = current closed candle)
 * @param {string} timeframe - '4h' | '1h'
 * @returns {Object|null}    signal or null
 */
function checkSignal(candles, timeframe) {
  if (candles.length < 20) return null;

  const sr         = getSupportResistance(candles);
  const trendlines = getTrendlines(candles);
  const last       = candles[candles.length - 1];
  const price      = last.close;
  const lastIdx    = candles.length - 1;

  // 1. Support zone → LONG
  for (const level of sr.support) {
    if (Math.abs(price - level) / level <= SR_TOLERANCE) {
      return _signal('LONG', price, `触及支撑位 ${level.toFixed(2)}`, timeframe, last);
    }
  }

  // 2. Uptrend line → LONG
  if (trendlines.uptrendLine) {
    const tp = trendlines.uptrendLine.priceAt(lastIdx);
    if (tp > 0 && Math.abs(price - tp) / tp <= TRENDLINE_TOLERANCE) {
      return _signal('LONG', price, `触及上升趋势线 ${tp.toFixed(2)}`, timeframe, last);
    }
  }

  // 3. Resistance zone → SHORT
  for (const level of sr.resistance) {
    if (Math.abs(price - level) / level <= SR_TOLERANCE) {
      return _signal('SHORT', price, `触及阻力位 ${level.toFixed(2)}`, timeframe, last);
    }
  }

  // 4. Downtrend line → SHORT
  if (trendlines.downtrendLine) {
    const tp = trendlines.downtrendLine.priceAt(lastIdx);
    if (tp > 0 && Math.abs(price - tp) / tp <= TRENDLINE_TOLERANCE) {
      return _signal('SHORT', price, `触及下降趋势线 ${tp.toFixed(2)}`, timeframe, last);
    }
  }

  return null;
}

function _signal(direction, price, reason, timeframe, candle) {
  return { direction, price, reason, timeframe, time: candle.time, candleTs: candle.timestamp };
}

module.exports = { checkSignal };
