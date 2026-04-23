/**
 * BTC/USDT 实时涨跌信号预测器
 * 数据源: Binance GET /api/v3/klines
 * 指标: EMA, RSI, MACD, Bollinger Bands, Volume Trend
 *
 * 环境变量:
 *   BINANCE_PROXY          代理地址，格式 host:port 或 host:port:user:pass 或 http://...
 *   BINANCE_API_BASE       覆盖 API 地址（默认 https://api.binance.com）
 *   BTC_SIGNAL_INTERVAL    持续监控刷新间隔（秒，默认 60）
 *   BTC_SIGNAL_RETRIES     单次请求失败最大重试次数（默认 3）
 */

import 'dotenv/config';
import { fetch as undiciFetch, ProxyAgent } from 'undici';
import { parseProxyLine } from './xbit-proxy.js';

const BINANCE_BASE = process.env.BINANCE_API_BASE || 'https://api.binance.com';
const SYMBOL = 'BTCUSDT';

// ─── 代理配置 ────────────────────────────────────────────────────────────────

function buildDispatcher() {
  // 优先 BINANCE_PROXY，其次自动读取系统代理环境变量
  const line = String(
    process.env.BINANCE_PROXY ||
    process.env.HTTPS_PROXY   ||
    process.env.https_proxy   ||
    process.env.HTTP_PROXY    ||
    process.env.http_proxy    || ''
  ).trim();
  if (!line) return undefined;
  const proxyUrl = parseProxyLine(line) || line;
  console.log(`[proxy] 使用代理: ${proxyUrl.replace(/:[^:@]+@/, ':***@')}`);
  return new ProxyAgent(proxyUrl);
}

const dispatcher = buildDispatcher();

function apiFetch(url) {
  if (dispatcher) return undiciFetch(url, { dispatcher });
  return undiciFetch(url);
}

// ─── 数据获取 ────────────────────────────────────────────────────────────────

async function fetchKlines(symbol, interval, limit = 100) {
  const url = `${BINANCE_BASE}/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Binance API error: ${res.status}`);
  const data = await res.json();
  return data.map(k => ({
    openTime:  k[0],
    open:      parseFloat(k[1]),
    high:      parseFloat(k[2]),
    low:       parseFloat(k[3]),
    close:     parseFloat(k[4]),
    volume:    parseFloat(k[5]),
    closeTime: k[6],
  }));
}

async function fetchTicker(symbol) {
  const url = `${BINANCE_BASE}/api/v3/ticker/24hr?symbol=${symbol}`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Binance ticker error: ${res.status}`);
  return res.json();
}

// ─── 技术指标计算 ─────────────────────────────────────────────────────────────

function calcEMA(closes, period) {
  const k = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  const result = new Array(period - 1).fill(null);
  result.push(ema);
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k);
    result.push(ema);
  }
  return result;
}

function calcRSI(closes, period = 14) {
  const result = new Array(period).fill(null);
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) avgGain += diff; else avgLoss += Math.abs(diff);
  }
  avgGain /= period;
  avgLoss /= period;
  result.push(100 - 100 / (1 + avgGain / (avgLoss || 1e-10)));
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? Math.abs(diff) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result.push(100 - 100 / (1 + avgGain / (avgLoss || 1e-10)));
  }
  return result;
}

function calcMACD(closes, fast = 12, slow = 26, signal = 9) {
  const emaFast = calcEMA(closes, fast);
  const emaSlow = calcEMA(closes, slow);
  const macdLine = emaFast.map((v, i) => (v !== null && emaSlow[i] !== null) ? v - emaSlow[i] : null);
  const validMacd = macdLine.filter(v => v !== null);
  const signalRaw = calcEMA(validMacd, signal);
  const signalLine = new Array(macdLine.length - validMacd.length).fill(null).concat(
    new Array(validMacd.length - signalRaw.length).fill(null).concat(signalRaw)
  );
  const histogram = macdLine.map((v, i) => (v !== null && signalLine[i] !== null) ? v - signalLine[i] : null);
  return { macdLine, signalLine, histogram };
}

function calcBollingerBands(closes, period = 20, stdDev = 2) {
  const result = { upper: [], middle: [], lower: [] };
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      result.upper.push(null); result.middle.push(null); result.lower.push(null);
      continue;
    }
    const slice = closes.slice(i - period + 1, i + 1);
    const mean = slice.reduce((a, b) => a + b, 0) / period;
    const variance = slice.reduce((sum, v) => sum + (v - mean) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    result.upper.push(mean + stdDev * sd);
    result.middle.push(mean);
    result.lower.push(mean - stdDev * sd);
  }
  return result;
}

// ─── 信号评分系统 ──────────────────────────────────────────────────────────────

function analyzeSignals(klines) {
  const closes  = klines.map(k => k.close);
  const volumes = klines.map(k => k.volume);
  const n = closes.length - 1; // 最新 index
  const signals = [];
  let score = 0; // 正数=看涨, 负数=看跌

  // ── EMA 趋势 (9/21/55) ──
  const ema9  = calcEMA(closes, 9);
  const ema21 = calcEMA(closes, 21);
  const ema55 = calcEMA(closes, 55);
  const price = closes[n];

  if (ema9[n] > ema21[n]) { score += 1; signals.push({ name: 'EMA9>EMA21', bias: '看涨↑', weight: 1 }); }
  else                     { score -= 1; signals.push({ name: 'EMA9<EMA21', bias: '看跌↓', weight: 1 }); }

  if (ema21[n] > ema55[n]) { score += 1; signals.push({ name: 'EMA21>EMA55', bias: '看涨↑', weight: 1 }); }
  else                     { score -= 1; signals.push({ name: 'EMA21<EMA55', bias: '看跌↓', weight: 1 }); }

  if (price > ema21[n])    { score += 1; signals.push({ name: '价格>EMA21', bias: '看涨↑', weight: 1 }); }
  else                     { score -= 1; signals.push({ name: '价格<EMA21', bias: '看跌↓', weight: 1 }); }

  // ── RSI ──
  const rsi = calcRSI(closes, 14);
  const rsiVal = rsi[n];
  if (rsiVal !== null) {
    if (rsiVal < 30)      { score += 2; signals.push({ name: `RSI=${rsiVal.toFixed(1)} 超卖`, bias: '强看涨↑↑', weight: 2 }); }
    else if (rsiVal > 70) { score -= 2; signals.push({ name: `RSI=${rsiVal.toFixed(1)} 超买`, bias: '强看跌↓↓', weight: 2 }); }
    else if (rsiVal < 50) { score -= 1; signals.push({ name: `RSI=${rsiVal.toFixed(1)} 弱势`, bias: '偏看跌↓', weight: 1 }); }
    else                  { score += 1; signals.push({ name: `RSI=${rsiVal.toFixed(1)} 强势`, bias: '偏看涨↑', weight: 1 }); }
  }

  // ── MACD ──
  const { macdLine, signalLine, histogram } = calcMACD(closes);
  const macdVal = macdLine[n];
  const sigVal  = signalLine[n];
  const histVal = histogram[n];
  const histPrev = histogram[n - 1];
  if (macdVal !== null && sigVal !== null) {
    if (macdVal > sigVal)    { score += 2; signals.push({ name: 'MACD金叉', bias: '看涨↑↑', weight: 2 }); }
    else                     { score -= 2; signals.push({ name: 'MACD死叉', bias: '看跌↓↓', weight: 2 }); }
  }
  if (histVal !== null && histPrev !== null) {
    if (histVal > 0 && histVal > histPrev) { score += 1; signals.push({ name: 'MACD柱扩张+', bias: '看涨↑', weight: 1 }); }
    if (histVal < 0 && histVal < histPrev) { score -= 1; signals.push({ name: 'MACD柱扩张-', bias: '看跌↓', weight: 1 }); }
  }

  // ── Bollinger Bands ──
  const bb = calcBollingerBands(closes);
  const bbUpper = bb.upper[n], bbMiddle = bb.middle[n], bbLower = bb.lower[n];
  if (bbUpper !== null) {
    const bbPos = (price - bbLower) / (bbUpper - bbLower);
    if (price < bbLower)       { score += 2; signals.push({ name: 'BB下轨突破(超卖)', bias: '强看涨↑↑', weight: 2 }); }
    else if (price > bbUpper)  { score -= 2; signals.push({ name: 'BB上轨突破(超买)', bias: '强看跌↓↓', weight: 2 }); }
    else if (bbPos < 0.3)      { score += 1; signals.push({ name: `BB低位 ${(bbPos*100).toFixed(0)}%`, bias: '偏看涨↑', weight: 1 }); }
    else if (bbPos > 0.7)      { score -= 1; signals.push({ name: `BB高位 ${(bbPos*100).toFixed(0)}%`, bias: '偏看跌↓', weight: 1 }); }
    else                       {             signals.push({ name: `BB中性 ${(bbPos*100).toFixed(0)}%`, bias: '中性─', weight: 0 }); }
  }

  // ── 成交量趋势 ──
  const avgVol5  = volumes.slice(n - 4, n + 1).reduce((a, b) => a + b, 0) / 5;
  const avgVol20 = volumes.slice(n - 19, n + 1).reduce((a, b) => a + b, 0) / 20;
  const volRatio = avgVol5 / avgVol20;
  if (volRatio > 1.5)      { signals.push({ name: `成交量放大 ${volRatio.toFixed(2)}x`, bias: '趋势确认', weight: 0 }); }
  else if (volRatio < 0.7) { signals.push({ name: `成交量萎缩 ${volRatio.toFixed(2)}x`, bias: '趋势疑问', weight: 0 }); }

  return { score, signals, ema9: ema9[n], ema21: ema21[n], ema55: ema55[n], rsiVal, macdVal, sigVal, histVal, bbUpper, bbMiddle, bbLower };
}

// ─── 日志工具 ─────────────────────────────────────────────────────────────────

function ts() {
  return new Date().toISOString();
}

function log(msg)  { console.log(`[${ts()}] ${msg}`); }
function warn(msg) { console.warn(`[${ts()}] WARN  ${msg}`); }
function err(msg)  { console.error(`[${ts()}] ERROR ${msg}`); }

// ─── 多周期汇总 ────────────────────────────────────────────────────────────────

function renderResult(timeframe, analysis, klines) {
  const { score, signals, rsiVal, macdVal, sigVal, bbUpper, bbMiddle, bbLower } = analysis;
  const maxScore = signals.reduce((sum, s) => sum + Math.abs(s.weight), 0);
  const confidence = maxScore > 0 ? Math.abs(score) / maxScore * 100 : 0;

  let direction, emoji;
  if      (score >= 4)  { direction = '强烈看涨'; emoji = '🟢🟢'; }
  else if (score >= 2)  { direction = '偏向看涨'; emoji = '🟢';   }
  else if (score >= 0)  { direction = '微弱看涨'; emoji = '🔵';   }
  else if (score >= -2) { direction = '微弱看跌'; emoji = '🟡';   }
  else if (score >= -4) { direction = '偏向看跌'; emoji = '🔴';   }
  else                  { direction = '强烈看跌'; emoji = '🔴🔴'; }

  console.log(`\n${'═'.repeat(56)}`);
  console.log(` ${emoji}  ${SYMBOL}  [${timeframe}]  →  ${direction}  (得分: ${score > 0 ? '+' : ''}${score})`);
  console.log(`${'─'.repeat(56)}`);
  console.log(` 置信度: ${confidence.toFixed(1)}%  |  最新价: $${klines[klines.length-1].close.toLocaleString()}`);
  console.log(`${'─'.repeat(56)}`);
  console.log(` 指标详情:`);
  for (const s of signals) {
    const icon = s.bias.includes('涨') ? ' ↑' : s.bias.includes('跌') ? ' ↓' : '  ';
    console.log(`  ${icon}  ${s.name.padEnd(22)} ${s.bias}`);
  }
  if (bbUpper !== null) {
    console.log(`${'─'.repeat(56)}`);
    console.log(` BB通道: 下 $${bbLower.toFixed(0)}  |  中 $${bbMiddle.toFixed(0)}  |  上 $${bbUpper.toFixed(0)}`);
  }
}

// ─── 主程序 ────────────────────────────────────────────────────────────────────

const MAX_RETRIES   = Math.max(0, parseInt(process.env.BTC_SIGNAL_RETRIES  || '3', 10));
const INTERVAL_SEC  = Math.max(10, parseInt(process.env.BTC_SIGNAL_INTERVAL || '60', 10));
const INTERVAL_MS   = INTERVAL_SEC * 1000;

async function fetchWithRetry(fn, label) {
  let lastErr;
  for (let attempt = 1; attempt <= MAX_RETRIES + 1; attempt++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (attempt <= MAX_RETRIES) {
        const delay = Math.min(2 ** attempt * 1000, 30_000);
        warn(`${label} 第${attempt}次失败，${delay / 1000}s 后重试: ${e.message}`);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
  throw lastErr;
}

async function runPrediction() {
  const now = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  log(`━━━ BTC/USDT 信号分析开始 ━━━  北京时间: ${now}`);

  try {
    const ticker = await fetchWithRetry(() => fetchTicker(SYMBOL), '24h行情');
    log(`24h行情  现价: $${parseFloat(ticker.lastPrice).toLocaleString()}  涨跌: ${parseFloat(ticker.priceChangePercent).toFixed(2)}%  量: ${parseFloat(ticker.volume).toFixed(0)} BTC`);

    const timeframes = [
      { label: '15分钟', interval: '15m', limit: 120 },
      { label: '1小时',  interval: '1h',  limit: 100 },
      { label: '4小时',  interval: '4h',  limit: 100 },
    ];

    const results = await Promise.all(
      timeframes.map(async tf => {
        const klines = await fetchWithRetry(
          () => fetchKlines(SYMBOL, tf.interval, tf.limit),
          `K线[${tf.label}]`
        );
        const analysis = analyzeSignals(klines);
        return { ...tf, analysis, klines };
      })
    );

    for (const r of results) {
      renderResult(r.label, r.analysis, r.klines);
    }

    const weighted = results[0].analysis.score * 1 + results[1].analysis.score * 2 + results[2].analysis.score * 3;
    console.log(`\n${'═'.repeat(56)}`);
    let overall;
    if      (weighted >=  8) overall = '🟢🟢 综合强烈看涨  —  建议关注做多机会';
    else if (weighted >=  4) overall = '🟢   综合偏向看涨  —  谨慎轻仓试多';
    else if (weighted >=  0) overall = '🔵   综合微弱看涨  —  观望为主';
    else if (weighted >= -4) overall = '🟡   综合微弱看跌  —  观望或轻仓空';
    else if (weighted >= -8) overall = '🔴   综合偏向看跌  —  谨慎轻仓试空';
    else                     overall = '🔴🔴 综合强烈看跌  —  建议关注做空机会';
    console.log(` 多周期综合 (权重1:2:3) 得分: ${weighted > 0 ? '+' : ''}${weighted}`);
    console.log(` ${overall}`);
    console.log(`${'═'.repeat(56)}`);
    console.log(`\n ⚠️  免责声明: 技术指标存在滞后性，仅供参考，不构成投资建议。\n`);
    log('━━━ 分析完成 ━━━');

  } catch (e) {
    const cause = e?.cause?.message || e?.cause?.code || '';
    err(`请求失败: ${e.message}${cause ? ` (${cause})` : ''}`);
    if (!dispatcher) {
      err('提示: 在 .env 中设置 BINANCE_PROXY=host:port 或 BINANCE_PROXY=host:port:user:pass');
    }
  }
}

// ─── 启动 ─────────────────────────────────────────────────────────────────────

const LOOP = process.argv.includes('--watch');

if (LOOP) {
  log(`持续监控模式启动 (每 ${INTERVAL_SEC}s 刷新，SIGINT/SIGTERM 退出)`);

  let timer;
  async function schedule() {
    await runPrediction();
    timer = setTimeout(schedule, INTERVAL_MS);
  }

  function shutdown(signal) {
    log(`收到 ${signal}，正在退出...`);
    clearTimeout(timer);
    process.exit(0);
  }
  process.on('SIGINT',  () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  schedule();
} else {
  runPrediction();
}
