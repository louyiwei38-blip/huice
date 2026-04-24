'use strict';

const ccxt = require('ccxt');

const exchange = new ccxt.binance({ enableRateLimit: true });

/**
 * Fetch OHLCV candles, supports requests > 1000 via pagination.
 * @param {string} symbol  e.g. 'BTC/USDT'
 * @param {string} timeframe  e.g. '1h', '4h'
 * @param {number} limit  total candles to fetch
 * @returns {Promise<Array>} array of candle objects
 */
async function fetchCandles(symbol, timeframe, limit = 1000) {
  const tfMs = exchange.parseTimeframe(timeframe) * 1000;
  const batchSize = 1000;

  if (limit <= batchSize) {
    const raw = await exchange.fetchOHLCV(symbol, timeframe, undefined, limit);
    return raw.map(formatCandle);
  }

  const allCandles = [];
  let since = Date.now() - limit * tfMs;

  while (allCandles.length < limit) {
    const batch = Math.min(batchSize, limit - allCandles.length);
    const raw = await exchange.fetchOHLCV(symbol, timeframe, since, batch);
    if (!raw || raw.length === 0) break;
    allCandles.push(...raw.map(formatCandle));
    since = raw[raw.length - 1][0] + tfMs;
    if (raw.length < batch) break;
  }

  return allCandles.slice(-limit);
}

function formatCandle([timestamp, open, high, low, close, volume]) {
  return { timestamp, open, high, low, close, volume, time: new Date(timestamp) };
}

module.exports = { fetchCandles };
