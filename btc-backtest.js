/**
 * BTCUSDT 1h 二元期权 策略回测
 * 数据源: Binance GET /api/v3/klines
 * 策略:   RSI(14) + KDJ(9,3,3) + Stochastic(14,3,3) + 支撑/阻力
 * 周期:   1h 主判断 + 15m 辅助确认
 * 回测:   过去 100 根 1h K线，每根视为一次 1h 到期交易
 *
 * 环境变量:
 *   BINANCE_PROXY          代理 (host:port 或 host:port:user:pass)
 *   BINANCE_API_BASE       覆盖 API 基地址
 *   BACKTEST_MIN_CONF      触发交易的最低置信度 % (默认 40)
 *   BACKTEST_DETAIL        设为 1 显示全部 100 条明细 (默认只显示最近20条)
 */

import 'dotenv/config';
import { fetch as undiciFetch, ProxyAgent } from 'undici';
import { parseProxyLine } from './xbit-proxy.js';

const BINANCE_BASE  = process.env.BINANCE_API_BASE   || 'https://api.binance.com';
const SYMBOL        = 'BTCUSDT';
const MIN_CONF      = parseFloat(process.env.BACKTEST_MIN_CONF || '40');
const SHOW_ALL      = process.env.BACKTEST_DETAIL === '1';

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
const apiFetch = (url) =>
  dispatcher ? undiciFetch(url, { dispatcher }) : undiciFetch(url);

// ─── 数据获取 ────────────────────────────────────────────────────────────────

async function fetchKlines(interval, limit) {
  const url = `${BINANCE_BASE}/api/v3/klines?symbol=${SYMBOL}&interval=${interval}&limit=${limit}`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Binance ${interval} HTTP ${res.status}`);
  return (await res.json()).map(k => ({
    openTime:  k[0],
    open:      parseFloat(k[1]),
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
    const lo = Math.min(...lows.slice(i - period + 1, i + 1));
    const hi = Math.max(...highs.slice(i - period + 1, i + 1));
    const rsv = hi === lo ? 50 : (closes[i] - lo) / (hi - lo) * 100;
    const k = (2 / 3) * pk + (1 / 3) * rsv;
    const d = (2 / 3) * pd + (1 / 3) * k;
    const j = 3 * k - 2 * d;
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
  // Slow %K = SMA(raw, smooth)
  const K = [];
  for (let i = 0; i < raw.length; i++) {
    if (i < kPeriod - 1 + smooth - 1) { K.push(null); continue; }
    const sl = raw.slice(i - smooth + 1, i + 1);
    K.push(sl.some(v => v === null) ? null : sl.reduce((a, b) => a + b, 0) / smooth);
  }
  // %D = SMA(K, smooth)
  const D = [];
  for (let i = 0; i < K.length; i++) {
    if (K[i] === null || i < smooth - 1) { D.push(null); continue; }
    const sl = K.slice(i - smooth + 1, i + 1);
    D.push(sl.some(v => v === null) ? null : sl.reduce((a, b) => a + b, 0) / smooth);
  }
  return { K, D };
}

// ─── 单周期评分 ───────────────────────────────────────────────────────────────
// 返回 score (-10 ~ +10) 及各指标值

function scoreSlice(highs, lows, closes) {
  const n = closes.length - 1;
  let score = 0;
  const ind = {};

  // RSI  (±3)
  const rsi = calcRSI(closes);
  ind.rsi = rsi[n];
  if (ind.rsi !== null) {
    if      (ind.rsi < 30) score += 3;
    else if (ind.rsi < 45) score += 1;
    else if (ind.rsi > 70) score -= 3;
    else if (ind.rsi > 55) score -= 1;
  }

  // KDJ J  (±3)
  const { J } = calcKDJ(highs, lows, closes);
  ind.kdjJ = J[n];
  if (ind.kdjJ !== null) {
    if      (ind.kdjJ < 10) score += 3;
    else if (ind.kdjJ < 30) score += 1;
    else if (ind.kdjJ > 90) score -= 3;
    else if (ind.kdjJ > 70) score -= 1;
  }

  // Stochastic %K  (±2)
  const { K: stK } = calcStoch(highs, lows, closes);
  ind.stochK = stK[n];
  if (ind.stochK !== null) {
    if      (ind.stochK < 20) score += 2;
    else if (ind.stochK < 40) score += 1;
    else if (ind.stochK > 80) score -= 2;
    else if (ind.stochK > 60) score -= 1;
  }

  // 支撑/阻力位置  (±2)
  const LB = 20;
  if (n >= LB - 1) {
    ind.support    = Math.min(...lows.slice(n - LB + 1, n + 1));
    ind.resistance = Math.max(...highs.slice(n - LB + 1, n + 1));
    ind.srPos = ind.resistance === ind.support
      ? 0.5
      : (closes[n] - ind.support) / (ind.resistance - ind.support);
    if      (ind.srPos < 0.15) score += 2;
    else if (ind.srPos < 0.35) score += 1;
    else if (ind.srPos > 0.85) score -= 2;
    else if (ind.srPos > 0.65) score -= 1;
  }

  return { score, ind };
}

// ─── 生成信号 ────────────────────────────────────────────────────────────────
// MAX_SCORE: 1h (3+3+2+2=10) + 15m 确认 (±2) = 12

const MAX_SCORE = 12;

function getSignal(klines1h, idx, klines15m) {
  const s1h = klines1h.slice(0, idx + 1);
  const { score: sc1h } = scoreSlice(
    s1h.map(k => k.high),
    s1h.map(k => k.low),
    s1h.map(k => k.close),
  );

  // 15m 确认：取截止到当前 1h K线收盘时的最新 80 根 15m K线
  const tMax = klines1h[idx].closeTime;
  const s15 = klines15m.filter(k => k.closeTime <= tMax).slice(-80);
  let sc15 = 0;
  if (s15.length >= 20) {
    const c15 = s15.map(k => k.close);
    const h15 = s15.map(k => k.high);
    const l15 = s15.map(k => k.low);
    const m = c15.length - 1;
    const rsi15 = calcRSI(c15);
    const { J: J15 } = calcKDJ(h15, l15, c15);
    const dir = sc1h >= 0 ? 1 : -1;
    if (rsi15[m] !== null) {
      sc15 += (dir === 1 && rsi15[m] < 50) ?  1 : 0;
      sc15 += (dir === -1 && rsi15[m] > 50) ? 1 : 0;
      sc15 += (dir === 1 && rsi15[m] > 65) ? -1 : 0;
      sc15 += (dir === -1 && rsi15[m] < 35) ? -1 : 0;
    }
    if (J15[m] !== null) {
      sc15 += (dir === 1 && J15[m] < 50) ?  1 : 0;
      sc15 += (dir === -1 && J15[m] > 50) ? 1 : 0;
      sc15 += (dir === 1 && J15[m] > 70) ? -1 : 0;
      sc15 += (dir === -1 && J15[m] < 30) ? -1 : 0;
    }
  }

  const total      = sc1h + sc15;
  const confidence = Math.min(100, (Math.abs(total) / MAX_SCORE) * 100);
  const direction  = total >= 0 ? 'UP' : 'DOWN';
  return { total, sc1h, sc15, confidence, direction };
}

// ─── 回测主程序 ───────────────────────────────────────────────────────────────

async function runBacktest() {
  const sep = '═'.repeat(64);
  const div = '─'.repeat(64);
  console.log(`\n${sep}`);
  console.log(` BTCUSDT  1h 二元期权  策略回测报告`);
  console.log(` 策略: RSI(14) + KDJ(9,3,3) + Stoch(14,3,3) + 支撑/阻力`);
  console.log(` 周期: 1h 主判断 + 15m 辅助 | 最低置信度: ${MIN_CONF}%`);
  console.log(`${sep}`);
  console.log('\n 获取历史 K 线数据...');

  // 需要 161 根 1h（60根指标预热 + 100根回测 + 1根收益验证）
  const [klines1h, klines15m] = await Promise.all([
    fetchKlines('1h', 161),
    fetchKlines('15m', 1000),
  ]);
  console.log(` 1h: ${klines1h.length} 根  |  15m: ${klines15m.length} 根`);

  const WARMUP = 60;
  const trades = [];

  for (let i = WARMUP; i < klines1h.length - 1; i++) {
    const entry  = klines1h[i].close;
    const exit   = klines1h[i + 1].close;
    const signal = getSignal(klines1h, i, klines15m);
    const pct    = ((exit - entry) / entry * 100);
    const outcome =
      exit === entry ? 'TIE' :
      (signal.direction === 'UP' ? (exit > entry ? 'WIN' : 'LOSS')
                                 : (exit < entry ? 'WIN' : 'LOSS'));
    trades.push({
      time:    new Date(klines1h[i].openTime).toISOString().slice(0, 16).replace('T', ' '),
      entry, exit, pct,
      signal, outcome,
    });
  }

  // ── 汇总统计 ────────────────────────────────────────────────────────────────

  const taken  = trades.filter(t => t.signal.confidence >= MIN_CONF);
  const wins   = taken.filter(t => t.outcome === 'WIN');
  const losses = taken.filter(t => t.outcome === 'LOSS');
  const ties   = taken.filter(t => t.outcome === 'TIE');
  const effective = wins.length + losses.length;
  const winRate = effective > 0 ? (wins.length / effective * 100) : 0;

  // 平均盈亏幅度
  const avgWinPct  = wins.length  > 0 ? wins.reduce((s, t) => s + Math.abs(t.pct), 0) / wins.length  : 0;
  const avgLossPct = losses.length > 0 ? losses.reduce((s, t) => s + Math.abs(t.pct), 0) / losses.length : 0;

  console.log(`\n${div}`);
  console.log(` 总信号: ${trades.length}  |  触发(置信≥${MIN_CONF}%): ${taken.length}  |  跳过: ${trades.length - taken.length}`);
  console.log(` 胜: ${wins.length}  负: ${losses.length}  平: ${ties.length}  →  胜率: ${winRate.toFixed(1)}%`);
  console.log(` 平均盈利幅度: +${avgWinPct.toFixed(3)}%  |  平均亏损幅度: -${avgLossPct.toFixed(3)}%`);

  // ── 置信度分段 ───────────────────────────────────────────────────────────────

  console.log(`${div}`);
  console.log(' 置信度分段胜率:');
  const buckets = [[30,40],[40,50],[50,60],[60,70],[70,80],[80,101]];
  for (const [lo, hi] of buckets) {
    const bt = taken.filter(t => t.signal.confidence >= lo && t.signal.confidence < hi);
    if (!bt.length) continue;
    const bw = bt.filter(t => t.outcome === 'WIN').length;
    const bl = bt.filter(t => t.outcome === 'LOSS').length;
    const br = bw + bl > 0 ? (bw / (bw + bl) * 100).toFixed(0) : '--';
    const bar = '█'.repeat(Math.round((bw / (bw + bl || 1)) * 20)).padEnd(20, '░');
    console.log(`  ${String(lo).padStart(3)}-${String(hi === 101 ? '100' : hi).padEnd(3)}%  ${String(bt.length).padStart(3)}次  胜率 ${String(br).padStart(4)}%  ${bar}`);
  }

  // ── 方向分布 ─────────────────────────────────────────────────────────────────

  const upTrades   = taken.filter(t => t.signal.direction === 'UP');
  const downTrades = taken.filter(t => t.signal.direction === 'DOWN');
  const upWins     = upTrades.filter(t => t.outcome === 'WIN').length;
  const downWins   = downTrades.filter(t => t.outcome === 'WIN').length;
  console.log(`${div}`);
  console.log(' 方向胜率:');
  console.log(`  看涨↑  ${upTrades.length} 次  胜率 ${upTrades.length > 0 ? (upWins / upTrades.filter(t => t.outcome !== 'TIE').length * 100).toFixed(0) : '--'}%`);
  console.log(`  看跌↓  ${downTrades.length} 次  胜率 ${downTrades.length > 0 ? (downWins / downTrades.filter(t => t.outcome !== 'TIE').length * 100).toFixed(0) : '--'}%`);

  // ── 明细 ─────────────────────────────────────────────────────────────────────

  const detail = SHOW_ALL ? taken : taken.slice(-20);
  console.log(`${div}`);
  console.log(` ${SHOW_ALL ? '全部' : '最近 20 条'}明细 (置信≥${MIN_CONF}%):`);
  console.log(`  ${'时间(UTC)'.padEnd(17)} ${'方向'.padEnd(5)} ${'置信'.padEnd(5)} ${'入场价'.padEnd(10)} ${'变动'.padEnd(9)} ${'结果'}`);
  for (const t of detail) {
    const dir  = t.signal.direction === 'UP' ? '看涨↑' : '看跌↓';
    const icon = t.outcome === 'WIN' ? '✓ WIN' : t.outcome === 'TIE' ? '─ TIE' : '✗ LOS';
    const sign = t.pct >= 0 ? '+' : '';
    console.log(
      `  ${t.time.padEnd(17)} ${dir}  ${String(t.signal.confidence.toFixed(0)).padStart(3)}%  ` +
      `$${String(t.entry.toFixed(0)).padStart(8)}  ${(sign + t.pct.toFixed(3) + '%').padStart(9)}  ${icon}`
    );
  }

  console.log(`\n${sep}`);
  console.log(` ⚠️  回测包含所有市况（含趋势行情），实盘只在震荡时操作，实际胜率应更高。`);
  console.log(` 提示: 设 BACKTEST_MIN_CONF=50 可查看更高置信度筛选下的胜率。`);
  console.log(`${sep}\n`);
}

runBacktest().catch(e => {
  const cause = e?.cause?.message || e?.cause?.code || '';
  console.error(`❌ 回测失败: ${e.message}${cause ? ` (${cause})` : ''}`);
  if (!dispatcher) {
    console.error('   提示: 网络无法直连 Binance，在 .env 设置 BINANCE_PROXY=host:port');
  }
});
