'use strict';

const { getSupportResistance, getTrendlines } = require('./indicators');

const SR_TOLERANCE        = 0.0005;  // 0.05% — support/resistance core zone
const TRENDLINE_TOLERANCE = 0.0003;  // 0.03% — trendline touch
const VOL_PERIOD          = 20;      // candles for average volume baseline
const VOL_MULTIPLIER      = 1.5;     // volume must exceed avg × this to trigger


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

  // Volume spike check: current volume vs recent average
  const volSlice  = candles.slice(-(VOL_PERIOD + 1), -1); // exclude current candle
  const avgVol    = volSlice.reduce((s, c) => s + c.volume, 0) / volSlice.length;
  const volSpike  = last.volume >= avgVol * VOL_MULTIPLIER;
  const volRatio  = (last.volume / avgVol).toFixed(2);

  // 1. Support zone → LONG (volume spike required)
  for (const level of sr.support) {
    if (volSpike && Math.abs(price - level) / level <= SR_TOLERANCE) {
      return _signal('LONG', price, `支撑位 ${level.toFixed(2)} 放量确认 (${volRatio}x均量)`, timeframe, last);
    }
  }

  // 2. Uptrend line → SHORT (price rises to upper channel resistance)
  if (trendlines.uptrendLine) {
    const tp = trendlines.uptrendLine.priceAt(lastIdx);
    if (tp > 0 && Math.abs(price - tp) / tp <= TRENDLINE_TOLERANCE) {
      return _signal('SHORT', price, `触及上升趋势线 ${tp.toFixed(2)}`, timeframe, last);
    }
  }

  // 3. Resistance zone → SHORT (volume spike required)
  for (const level of sr.resistance) {
    if (volSpike && Math.abs(price - level) / level <= SR_TOLERANCE) {
      return _signal('SHORT', price, `阻力位 ${level.toFixed(2)} 放量确认 (${volRatio}x均量)`, timeframe, last);
    }
  }

  // 4. Downtrend line → LONG (price falls to lower channel support)
  if (trendlines.downtrendLine) {
    const tp = trendlines.downtrendLine.priceAt(lastIdx);
    if (tp > 0 && Math.abs(price - tp) / tp <= TRENDLINE_TOLERANCE) {
      return _signal('LONG', price, `触及下降趋势线 ${tp.toFixed(2)}`, timeframe, last);
    }
  }

  return null;
}

function _signal(direction, price, reason, timeframe, candle) {
  return { direction, price, reason, timeframe, time: candle.time, candleTs: candle.timestamp };
}

module.exports = { checkSignal, SR_TOLERANCE, TRENDLINE_TOLERANCE };
