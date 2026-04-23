/**
 * BTCUSDT 二元期权 震荡行情信号
 * 周期: 1h 主判断 + 15m 辅助确认
 * 指标: RSI(14) + KDJ(9,3,3) + Stochastic(14,3,3) + 支撑/阻力
 * 输出: 单行极简信号，每分钟刷新
 *
 * 用法:
 *   node btc-oscillation-signal.js              单次输出
 *   node btc-oscillation-signal.js --watch      持续监控（每分钟）
 *   node btc-oscillation-signal.js --backtest   回测过去100次交易
 *
 * 环境变量:
 *   BINANCE_PROXY       代理地址 (host:port 或 host:port:user:pass)
 *   BINANCE_API_BASE    覆盖 API 地址（默认 https://api.binance.com）
 *   SIGNAL_INTERVAL     --watch 刷新间隔（秒，默认 60）
 *   BACKTEST_MIN_CONF   回测触发最低置信度 % (默认 40)
 */

import 'dotenv/config';
import { fetch as undiciFetch, ProxyAgent } from 'undici';
import { parseProxyLine } from './xbit-proxy.js';

const BINANCE_BASE = process.env.BINANCE_API_BASE || 'https://api.binance.com';
const SYMBOL       = 'BTCUSDT';
const INTERVAL_SEC = Math.max(10, parseInt(process.env.SIGNAL_INTERVAL || '60', 10));
const INTERVAL_MS  = INTERVAL_SEC * 1000;

// MAX_SCORE: 1h (RSI ±3 + KDJ_J ±3 + Stoch ±2 + S/R ±2 = ±10) + 15m 确认 ±2 = ±12
const MAX_SCORE = 12;
const MIN_CONF  = parseFloat(process.env.BACKTEST_MIN_CONF || '40');

// ─── 代理 ────────────────────────────────────────────────────────────────────

function buildDispatcher() {
  const line = String(
    process.env.BINANCE_PROXY ||
    process.env.HTTPS_PROXY   || process.env.https_proxy ||
    process.env.HTTP_PROXY    || process.env.http_proxy  || ''
  ).trim();
  if (!line) return undefined;
  return new ProxyAgent(parseProxyLine(line) || line);
}

const dispatcher = buildDispatcher();
const apiFetch   = (url) =>
  dispatcher ? undiciFetch(url, { dispatcher }) : undiciFetch(url);

// ─── 数据获取 ────────────────────────────────────────────────────────────────

async function fetchKlines(interval, limit) {
  const url = `${BINANCE_BASE}/api/v3/klines?symbol=${SYMBOL}&interval=${interval}&limit=${limit}`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Binance ${interval} HTTP ${res.status}`);
  return (await res.json()).map(k => ({
    openTime:  k[0],
    high:      parseFloat(k[2]),
    low:       parseFloat(k[3]),
    close:     parseFloat(k[4]),
    closeTime: k[6],
  }));
}

// ─── 技术指标 ────────────────────────────────────────────────────────────────

function calcRSI(closes, period = 14) {
  const out = new Array(period).fill(null);
  if (closes.length <= period) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) gain += d; else loss -= d;
  }
  gain /= period; loss /= period;
  out.push(100 - 100 / (1 + gain / (loss || 1e-10)));
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    gain = (gain * (period - 1) + (d > 0 ? d : 0)) / period;
    loss = (loss * (period - 1) + (d < 0 ? -d : 0)) / period;
    out.push(100 - 100 / (1 + gain / (loss || 1e-10)));
  }
  return out;
}

function calcKDJ(highs, lows, closes, period = 9) {
  const K = [], D = [], J = [];
  let pk = 50, pd = 50;
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) { K.push(null); D.push(null); J.push(null); continue; }
    const lo  = Math.min(...lows.slice(i - period + 1, i + 1));
    const hi  = Math.max(...highs.slice(i - period + 1, i + 1));
    const rsv = hi === lo ? 50 : (closes[i] - lo) / (hi - lo) * 100;
    const k   = (2 / 3) * pk + (1 / 3) * rsv;
    const d   = (2 / 3) * pd + (1 / 3) * k;
    const j   = 3 * k - 2 * d;
    K.push(k); D.push(d); J.push(j);
    pk = k; pd = d;
  }
  return { K, D, J };
}

function calcStoch(highs, lows, closes, kPeriod = 14, smooth = 3) {
  const raw = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < kPeriod - 1) { raw.push(null); continue; }
    const lo = Math.min(...lows.slice(i - kPeriod + 1, i + 1));
    const hi = Math.max(...highs.slice(i - kPeriod + 1, i + 1));
    raw.push(hi === lo ? 50 : (closes[i] - lo) / (hi - lo) * 100);
  }
  const K = [];
  for (let i = 0; i < raw.length; i++) {
    if (i < kPeriod - 1 + smooth - 1) { K.push(null); continue; }
    const sl = raw.slice(i - smooth + 1, i + 1);
    K.push(sl.some(v => v === null) ? null : sl.reduce((a, b) => a + b, 0) / smooth);
  }
  const D = [];
  for (let i = 0; i < K.length; i++) {
    if (K[i] === null || i < smooth - 1) { D.push(null); continue; }
    const sl = K.slice(i - smooth + 1, i + 1);
    D.push(sl.some(v => v === null) ? null : sl.reduce((a, b) => a + b, 0) / smooth);
  }
  return { K, D };
}

// ─── 1h 主判断评分 ────────────────────────────────────────────────────────────
// RSI ±3 | KDJ_J ±3 | Stoch ±2 | S/R ±2  →  合计 ±10

function score1h(highs, lows, closes) {
  const n = closes.length - 1;
  let score = 0;

  const rsi = calcRSI(closes);
  if (rsi[n] !== null) {
    if      (rsi[n] < 30) score += 3;
    else if (rsi[n] < 45) score += 1;
    else if (rsi[n] > 70) score -= 3;
    else if (rsi[n] > 55) score -= 1;
  }

  const { J } = calcKDJ(highs, lows, closes);
  if (J[n] !== null) {
    if      (J[n] < 10) score += 3;
    else if (J[n] < 30) score += 1;
    else if (J[n] > 90) score -= 3;
    else if (J[n] > 70) score -= 1;
  }

  const { K: stK } = calcStoch(highs, lows, closes);
  if (stK[n] !== null) {
    if      (stK[n] < 20) score += 2;
    else if (stK[n] < 40) score += 1;
    else if (stK[n] > 80) score -= 2;
    else if (stK[n] > 60) score -= 1;
  }

  const LB = 20;
  if (n >= LB - 1) {
    const support    = Math.min(...lows.slice(n - LB + 1, n + 1));
    const resistance = Math.max(...highs.slice(n - LB + 1, n + 1));
    const srPos      = resistance === support
      ? 0.5
      : (closes[n] - support) / (resistance - support);
    if      (srPos < 0.15) score += 2;
    else if (srPos < 0.35) score += 1;
    else if (srPos > 0.85) score -= 2;
    else if (srPos > 0.65) score -= 1;
  }

  return score;
}

// ─── 15m 辅助确认 ─────────────────────────────────────────────────────────────
// 方向一致加分，反向减分，合计 ±2

function confirm15m(klines, dir1h) {
  if (klines.length < 20) return 0;
  const n = klines.length - 1;
  const c = klines.map(k => k.close);
  const h = klines.map(k => k.high);
  const l = klines.map(k => k.low);
  let sc = 0;

  const rsi15 = calcRSI(c);
  const { J: J15 } = calcKDJ(h, l, c);

  if (rsi15[n] !== null) {
    if (dir1h ===  1 && rsi15[n] < 50) sc += 1;
    if (dir1h === -1 && rsi15[n] > 50) sc += 1;
    if (dir1h ===  1 && rsi15[n] > 65) sc -= 1;
    if (dir1h === -1 && rsi15[n] < 35) sc -= 1;
  }
  if (J15[n] !== null) {
    if (dir1h ===  1 && J15[n] < 50) sc += 1;
    if (dir1h === -1 && J15[n] > 50) sc += 1;
    if (dir1h ===  1 && J15[n] > 70) sc -= 1;
    if (dir1h === -1 && J15[n] < 30) sc -= 1;
  }
  return sc;
}

// ─── 回测辅助：单根K线信号 ──────────────────────────────────────────────────────

function getSignal(klines1h, idx, klines15m) {
  const s1h   = klines1h.slice(0, idx + 1);
  const sc1h  = score1h(
    s1h.map(k => k.high),
    s1h.map(k => k.low),
    s1h.map(k => k.close),
  );
  const dir1h = sc1h >= 0 ? 1 : -1;
  const tMax  = klines1h[idx].closeTime;
  const s15   = klines15m.filter(k => k.closeTime <= tMax).slice(-80);
  const sc15  = confirm15m(s15, dir1h);
  const total = sc1h + sc15;
  return {
    total,
    confidence: Math.round(Math.min(100, (Math.abs(total) / MAX_SCORE) * 100)),
    direction:  total >= 0 ? 'UP' : 'DOWN',
  };
}

// ─── 回测主程序 ───────────────────────────────────────────────────────────────

async function runBacktest() {
  const SEP = '═'.repeat(62);
  const DIV = '─'.repeat(62);
  console.log(`\n${SEP}`);
  console.log(` BTCUSDT  1h 二元期权  震荡策略回测`);
  console.log(` 指标: RSI(14) + KDJ(9,3,3) + Stoch(14,3,3) + S/R(20)`);
  console.log(` 周期: 1h 主判断 + 15m 辅助确认 | 最低置信度: ${MIN_CONF}%`);
  console.log(`${SEP}`);
  console.log(' 获取历史 K 线数据...');

  // 60根预热 + 100根回测 + 1根验证 = 161根1h；15m取1000根覆盖回测窗口
  const WARMUP = 60;
  const [k1h, k15m] = await Promise.all([
    fetchKlines('1h',  WARMUP + 100 + 1),
    fetchKlines('15m', 1000),
  ]);
  console.log(` 1h: ${k1h.length} 根  |  15m: ${k15m.length} 根\n${DIV}`);

  const trades = [];
  for (let i = WARMUP; i < k1h.length - 1; i++) {
    const entry   = k1h[i].close;
    const exit    = k1h[i + 1].close;
    const signal  = getSignal(k1h, i, k15m);
    const move    = (exit - entry) / entry * 100;
    const outcome = exit === entry ? 'TIE'
      : signal.direction === 'UP'
        ? (exit > entry ? 'WIN' : 'LOS')
        : (exit < entry ? 'WIN' : 'LOS');
    trades.push({
      time: new Date(k1h[i].openTime).toISOString().slice(0, 16).replace('T', ' '),
      entry, exit, move, signal, outcome,
    });
  }

  // ── 汇总 ─────────────────────────────────────────────────────────────────────

  const taken   = trades.filter(t => t.signal.confidence >= MIN_CONF);
  const wins    = taken.filter(t => t.outcome === 'WIN');
  const losses  = taken.filter(t => t.outcome === 'LOS');
  const ties    = taken.filter(t => t.outcome === 'TIE');
  const eff     = wins.length + losses.length;
  const winRate = eff > 0 ? wins.length / eff * 100 : 0;

  // 100根1h K线 = 100小时 ≈ 4.167天；触发次数除以天数 = 交易频率
  const spanDays = trades.length / 24;
  const freq     = taken.length / spanDays;

  console.log(` 总信号: ${trades.length}  |  触发(置信≥${MIN_CONF}%): ${taken.length}  |  跳过: ${trades.length - taken.length}`);
  console.log(` 胜: ${wins.length}  负: ${losses.length}  平: ${ties.length}  →  胜率: ${winRate.toFixed(1)}%`);
  console.log(` 交易频率: ${freq.toFixed(1)} 次/天  (回测跨度 ≈ ${spanDays.toFixed(1)} 天)`);

  // ── 置信度分段胜率 ────────────────────────────────────────────────────────────

  console.log(`${DIV}`);
  console.log(' 置信度分段胜率:');
  const buckets = [[30,40],[40,50],[50,60],[60,70],[70,80],[80,101]];
  for (const [lo, hi] of buckets) {
    const bt = taken.filter(t => t.signal.confidence >= lo && t.signal.confidence < hi);
    if (!bt.length) continue;
    const bw = bt.filter(t => t.outcome === 'WIN').length;
    const bl = bt.filter(t => t.outcome === 'LOS').length;
    const br = bw + bl > 0 ? (bw / (bw + bl) * 100).toFixed(0) : '--';
    const bar = '█'.repeat(Math.round((bw / (bw + bl || 1)) * 20)).padEnd(20, '░');
    const hiLabel = hi === 101 ? '100' : String(hi);
    console.log(`  ${String(lo).padStart(3)}-${hiLabel.padEnd(3)}%  ${String(bt.length).padStart(3)}次  胜率 ${String(br).padStart(3)}%  ${bar}`);
  }

  // ── 方向分布 ──────────────────────────────────────────────────────────────────

  const upT  = taken.filter(t => t.signal.direction === 'UP');
  const dnT  = taken.filter(t => t.signal.direction === 'DOWN');
  const upW  = upT.filter(t => t.outcome === 'WIN').length;
  const dnW  = dnT.filter(t => t.outcome === 'WIN').length;
  const upEff = upT.filter(t => t.outcome !== 'TIE').length;
  const dnEff = dnT.filter(t => t.outcome !== 'TIE').length;
  console.log(`${DIV}`);
  console.log(' 方向胜率:');
  console.log(`  看涨↑  ${upT.length} 次  胜率 ${upEff > 0 ? (upW / upEff * 100).toFixed(0) : '--'}%`);
  console.log(`  看跌↓  ${dnT.length} 次  胜率 ${dnEff > 0 ? (dnW / dnEff * 100).toFixed(0) : '--'}%`);

  // ── 最近 20 条明细 ────────────────────────────────────────────────────────────

  console.log(`${DIV}`);
  console.log(` 最近 20 条明细 (置信≥${MIN_CONF}%):`);
  console.log(`  ${'时间(UTC)'.padEnd(17)} ${'方向'.padEnd(5)} ${'置信'.padEnd(5)} ${'入场价'.padEnd(9)} ${'变动'.padEnd(9)} 结果`);
  for (const t of taken.slice(-20)) {
    const dir  = t.signal.direction === 'UP' ? '看涨↑' : '看跌↓';
    const icon = t.outcome === 'WIN' ? '✓ WIN' : t.outcome === 'TIE' ? '─ TIE' : '✗ LOS';
    const sign = t.move >= 0 ? '+' : '';
    console.log(
      `  ${t.time.padEnd(17)} ${dir}  ${String(t.signal.confidence).padStart(3)}%` +
      `  $${String(t.entry.toFixed(0)).padStart(7)}` +
      `  ${(sign + t.move.toFixed(3) + '%').padStart(9)}  ${icon}`
    );
  }

  console.log(`\n${SEP}`);
  console.log(` ⚠  回测含所有市况；实盘只在震荡行情操作，实际胜率应更高。`);
  console.log(` 提示: 设 BACKTEST_MIN_CONF=50 查看更高置信度筛选下的胜率。`);
  console.log(`${SEP}\n`);
}

// ─── 主程序 ───────────────────────────────────────────────────────────────────

async function runSignal() {
  const [k1h, k15m] = await Promise.all([
    fetchKlines('1h',  100),
    fetchKlines('15m', 80),
  ]);

  const price  = k1h[k1h.length - 1].close;
  const sc1h   = score1h(
    k1h.map(k => k.high),
    k1h.map(k => k.low),
    k1h.map(k => k.close),
  );
  const dir1h  = sc1h >= 0 ? 1 : -1;
  const sc15   = confirm15m(k15m, dir1h);
  const total  = sc1h + sc15;

  const confidence = Math.round(Math.min(100, (Math.abs(total) / MAX_SCORE) * 100));
  const direction  = total >= 0 ? '看涨 ↑' : '看跌 ↓';
  const priceStr   = price.toLocaleString('en-US', { maximumFractionDigits: 0 });

  console.log(`[${new Date().toISOString()}] ${SYMBOL} → ${direction}  置信度 ${confidence}%  |  $${priceStr}`);
}

// ─── 启动 ─────────────────────────────────────────────────────────────────────

const WATCH    = process.argv.includes('--watch');
const BACKTEST = process.argv.includes('--backtest');

if (BACKTEST) {
  runBacktest().catch(e => {
    const cause = e?.cause?.message || e?.cause?.code || '';
    console.error(`[${new Date().toISOString()}] ERROR ${e.message}${cause ? ` (${cause})` : ''}`);
    process.exit(1);
  });
} else if (WATCH) {
  let timer;
  async function schedule() {
    try {
      await runSignal();
    } catch (e) {
      const cause = e?.cause?.message || e?.cause?.code || '';
      console.error(`[${new Date().toISOString()}] ERROR ${e.message}${cause ? ` (${cause})` : ''}`);
    }
    timer = setTimeout(schedule, INTERVAL_MS);
  }
  process.on('SIGINT',  () => { clearTimeout(timer); process.exit(0); });
  process.on('SIGTERM', () => { clearTimeout(timer); process.exit(0); });
  schedule();
} else {
  runSignal().catch(e => {
    const cause = e?.cause?.message || e?.cause?.code || '';
    console.error(`[${new Date().toISOString()}] ERROR ${e.message}${cause ? ` (${cause})` : ''}`);
    process.exit(1);
  });
}
