'use strict';

const SWING_WINDOW = 5;    // candles on each side for pivot detection
const MERGE_THRESHOLD = 0.005; // merge S/R levels within 0.5% of each other

// ─── Swing Points ────────────────────────────────────────────────────────────

function findSwingHighs(candles, window = SWING_WINDOW) {
  const result = [];
  for (let i = window; i < candles.length - window; i++) {
    const h = candles[i].high;
    let pivot = true;
    for (let j = i - window; j <= i + window; j++) {
      if (j !== i && candles[j].high >= h) { pivot = false; break; }
    }
    if (pivot) result.push({ index: i, price: h, time: candles[i].time });
  }
  return result;
}

function findSwingLows(candles, window = SWING_WINDOW) {
  const result = [];
  for (let i = window; i < candles.length - window; i++) {
    const l = candles[i].low;
    let pivot = true;
    for (let j = i - window; j <= i + window; j++) {
      if (j !== i && candles[j].low <= l) { pivot = false; break; }
    }
    if (pivot) result.push({ index: i, price: l, time: candles[i].time });
  }
  return result;
}

// ─── Volume Clusters ─────────────────────────────────────────────────────────

function getVolumeClusters(candles, bins = 200) {
  if (candles.length < 10) return [];
  const prices = candles.map(c => (c.high + c.low) / 2);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  if (maxP <= minP) return [minP];

  const binSize = (maxP - minP) / bins;
  const volBins = new Array(bins).fill(0);

  for (const c of candles) {
    const idx = Math.min(Math.floor(((c.high + c.low) / 2 - minP) / binSize), bins - 1);
    volBins[idx] += c.volume;
  }

  const avgVol = volBins.reduce((a, b) => a + b, 0) / bins;
  const clusters = [];
  for (let i = 0; i < bins; i++) {
    if (volBins[i] > avgVol * 1.5) {
      clusters.push(minP + (i + 0.5) * binSize);
    }
  }
  return clusters;
}

// ─── Level Merging ───────────────────────────────────────────────────────────

function mergeLevels(levels, threshold = MERGE_THRESHOLD) {
  if (!levels.length) return [];
  const sorted = [...levels].sort((a, b) => a - b);
  const merged = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const prev = merged[merged.length - 1];
    if ((sorted[i] - prev) / prev <= threshold) {
      merged[merged.length - 1] = (prev + sorted[i]) / 2;
    } else {
      merged.push(sorted[i]);
    }
  }
  return merged;
}

// ─── Support / Resistance ─────────────────────────────────────────────────────

/**
 * Returns merged support and resistance levels for the given candle array.
 */
function getSupportResistance(candles) {
  const swingHighs = findSwingHighs(candles);
  const swingLows  = findSwingLows(candles);
  const clusters   = getVolumeClusters(candles);

  const recent     = candles.slice(-Math.min(50, candles.length));
  const rangeHigh  = Math.max(...recent.map(c => c.high));
  const rangeLow   = Math.min(...recent.map(c => c.low));

  return {
    resistance: mergeLevels([...swingHighs.map(s => s.price), ...clusters, rangeHigh]),
    support:    mergeLevels([...swingLows.map(s => s.price),  ...clusters, rangeLow]),
  };
}

// ─── Trendlines ──────────────────────────────────────────────────────────────

/**
 * Returns uptrend (connecting last 2 swing lows) and downtrend (connecting
 * last 2 swing highs) lines, each with a priceAt(index) evaluator.
 */
function getTrendlines(candles) {
  const swingHighs = findSwingHighs(candles);
  const swingLows  = findSwingLows(candles);

  let uptrendLine   = null;
  let downtrendLine = null;

  if (swingLows.length >= 2) {
    const p1 = swingLows[swingLows.length - 2];
    const p2 = swingLows[swingLows.length - 1];
    const slope = (p2.price - p1.price) / (p2.index - p1.index);
    uptrendLine = {
      p1, p2, slope,
      priceAt: (idx) => p2.price + slope * (idx - p2.index),
    };
  }

  if (swingHighs.length >= 2) {
    const p1 = swingHighs[swingHighs.length - 2];
    const p2 = swingHighs[swingHighs.length - 1];
    const slope = (p2.price - p1.price) / (p2.index - p1.index);
    downtrendLine = {
      p1, p2, slope,
      priceAt: (idx) => p2.price + slope * (idx - p2.index),
    };
  }

  return { uptrendLine, downtrendLine };
}

// ─── ADX (Average Directional Index) ─────────────────────────────────────────

/**
 * Wilder's smoothing: initial = sum of first `period` values,
 * then smooth = prev - prev/period + current
 */
function wildersSmooth(arr, period) {
  let smoothed = arr.slice(0, period).reduce((s, v) => s + v, 0);
  const result = [smoothed];
  for (let i = period; i < arr.length; i++) {
    smoothed = smoothed - smoothed / period + arr[i];
    result.push(smoothed);
  }
  return result;
}

/**
 * Calculate ADX value for the last candle.
 * @param {Array}  candles - OHLCV candle array
 * @param {number} period  - default 14
 * @returns {number|null}  ADX value, or null if insufficient data
 */
function calculateADX(candles, period = 14) {
  if (candles.length < period * 2 + 1) return null;

  const trs = [], plusDMs = [], minusDMs = [];

  for (let i = 1; i < candles.length; i++) {
    const c = candles[i], p = candles[i - 1];
    trs.push(Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close)));
    const up = c.high - p.high, dn = p.low - c.low;
    plusDMs.push(up > dn && up > 0 ? up : 0);
    minusDMs.push(dn > up && dn > 0 ? dn : 0);
  }

  const sTR    = wildersSmooth(trs, period);
  const sPDM   = wildersSmooth(plusDMs, period);
  const sMDM   = wildersSmooth(minusDMs, period);

  const dxArr = sTR.map((tr, i) => {
    const pDI = (sPDM[i] / tr) * 100;
    const mDI = (sMDM[i] / tr) * 100;
    const sum = pDI + mDI;
    return sum === 0 ? 0 : (Math.abs(pDI - mDI) / sum) * 100;
  });

  const adxArr = wildersSmooth(dxArr, period);
  return adxArr[adxArr.length - 1];
}

/**
 * Returns true when market is ranging (non-trending).
 * Uses 1H candles; ADX < 25 indicates weak trend = oscillating.
 */
function isRanging(candles, period = 14, threshold = 25) {
  const adx = calculateADX(candles, period);
  if (adx === null) return true; // default allow if insufficient data
  return adx < threshold;
}

module.exports = { getSupportResistance, getTrendlines, findSwingHighs, findSwingLows, calculateADX, isRanging };
