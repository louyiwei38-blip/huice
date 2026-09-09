"""
RFA-30 事件合约规则与对照策略。禁止改参数。仓位与结算在回测层执行。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ----- 固定参数（不得修改） -----
WIN_PAYOFF = 0.85
LOSS_PAYOFF = -1.0
HOLD_MINUTES = 30
MIN_ENTRY_GAP_MINUTES = 30
WARMUP_BARS = 200
ATR_PCT_BTC = 0.0010
ATR_PCT_ETH = 0.0013
TR5_MULT = 2.2
ADX_TREND = 22.0
ADX_RANGE = 20.0
A_QUALITY_MIN = 2
B_QUALITY_MIN = 2
BB_PERIOD = 20
BB_STD = 2.0
VWAP_ATR_MULT = 1.6
FUNDING_HOURS = (0, 8, 16)
FUNDING_BEFORE_MIN = 15
FUNDING_AFTER_MIN = 10
EV_WINRATE_THRESHOLD = 1.0 / 1.85  # ≈ 0.540540...
# 简化策略 RSI7_EMA8：RSI(7) 阈值取自原文 B2，EMA8 交叉取自原文 A3，不另搜参
RSI7_OVERSOLD = 22.0
RSI7_OVERBOUGHT = 78.0
# RSI_BB：只在 IS 网格选定后冻结；OOS 仅作确认，禁止再改
# 事件合约无资金费，RSI_BB 默认不跳过资金费窗口（ignore_funding=True）
RSI_BB_PERIOD = 7
RSI_BB_OS = 20.0
RSI_BB_OB = 80.0
RSI_BB_K = 2.2

SYMBOL_ATR_PCT = {"BTCUSDT": ATR_PCT_BTC, "ETHUSDT": ATR_PCT_ETH}


def in_funding_window(index: pd.DatetimeIndex) -> np.ndarray:
    """
    资金费窗口：UTC 00:00 / 08:00 / 16:00。
    T 落在 [结算-15min, 结算+10min) 则过滤。
    """
    idx = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    minutes = idx.hour * 60 + idx.minute
    midnight = (minutes >= 23 * 60 + FUNDING_BEFORE_MIN) | (minutes < FUNDING_AFTER_MIN)
    eight = (minutes >= 7 * 60 + FUNDING_BEFORE_MIN) & (minutes < 8 * 60 + FUNDING_AFTER_MIN)
    sixteen = (minutes >= 15 * 60 + FUNDING_BEFORE_MIN) & (minutes < 16 * 60 + FUNDING_AFTER_MIN)
    return np.asarray(midnight | eight | sixteen)


def merge_ab_bias(bias_a: np.ndarray, bias_b: np.ndarray) -> np.ndarray:
    """A、B 同时给出非零方向则空仓。"""
    conflict = (bias_a != 0) & (bias_b != 0)
    out = np.zeros(len(bias_a), dtype=int)
    out = np.where((bias_a != 0) & ~conflict, bias_a, out)
    out = np.where((bias_b != 0) & ~conflict, bias_b, out)
    return out


def _regime(adx: np.ndarray, ema20: np.ndarray, ema50: np.ndarray, close: np.ndarray) -> np.ndarray:
    trend_up = (adx >= ADX_TREND) & (ema20 > ema50) & (close > ema20)
    trend_down = (adx >= ADX_TREND) & (ema20 < ema50) & (close < ema20)
    rng = adx < ADX_RANGE
    out = np.full(close.shape, "NO_TRADE", dtype=object)
    out[rng] = "RANGE"
    out[trend_up] = "TREND_UP"
    out[trend_down] = "TREND_DOWN"
    return out


def evaluate_rfa30(feat: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    在每根 5m 收盘上计算 regime / setup / bias / quality。
    不含持仓门控；持仓与结算由 backtest 处理。
    """
    n = len(feat)
    idx = feat.index
    close5 = feat["close"].to_numpy()
    open5 = feat["open"].to_numpy()
    prev5 = feat["prev_close"].to_numpy()
    ema8 = feat["ema8_5"].to_numpy()
    atr5 = feat["atr14_5"].to_numpy()
    tr5 = feat["tr5"].to_numpy()
    c15 = feat["m15_close"].to_numpy()
    ema20_15 = feat["m15_ema20"].to_numpy()
    ema50_15 = feat["m15_ema50"].to_numpy()
    atr15 = feat["m15_atr14"].to_numpy()
    rsi14 = feat["m15_rsi14"].to_numpy()
    rsi7 = feat["m15_rsi7"].to_numpy()
    bb_u = feat["m15_bb_upper"].to_numpy()
    bb_l = feat["m15_bb_lower"].to_numpy()
    vwap = feat["vwap"].to_numpy()
    c1h = feat["h1_close"].to_numpy()
    ema20_1h = feat["h1_ema20"].to_numpy()
    ema50_1h = feat["h1_ema50"].to_numpy()
    adx = feat["h1_adx"].to_numpy()

    regime = _regime(adx, ema20_1h, ema50_1h, c1h)
    atr15_pct = atr15 / c15
    thr = SYMBOL_ATR_PCT[symbol]
    vol_ok = (
        feat["warm"].to_numpy()
        & np.isfinite(atr15_pct)
        & (atr15_pct >= thr)
        & np.isfinite(tr5)
        & np.isfinite(atr5)
        & ~(tr5 > TR5_MULT * atr5)
        & ~in_funding_window(idx)
    )

    # ----- 信号 A TREND_PULLBACK（仅 TREND_UP / TREND_DOWN） -----
    in_trend = (regime == "TREND_UP") | (regime == "TREND_DOWN")
    a1_long = (c15 >= ema20_15 - 0.8 * atr15) & (c15 <= ema20_15 + 0.15 * atr15) & (c15 > ema50_15)
    a2_long = (rsi14 >= 38.0) & (rsi14 <= 52.0)
    a3_long = (close5 > ema8) & (prev5 <= ema8)
    q_a_long = a1_long.astype(int) + a2_long.astype(int) + a3_long.astype(int)

    a1_short = (c15 >= ema20_15 - 0.15 * atr15) & (c15 <= ema20_15 + 0.8 * atr15) & (c15 < ema50_15)
    a2_short = (rsi14 >= 48.0) & (rsi14 <= 62.0)
    a3_short = (close5 < ema8) & (prev5 >= ema8)
    q_a_short = a1_short.astype(int) + a2_short.astype(int) + a3_short.astype(int)

    a_long = in_trend & (q_a_long >= A_QUALITY_MIN)
    a_short = in_trend & (q_a_short >= A_QUALITY_MIN)

    # ----- 信号 B RANGE_FADE（仅 RANGE） -----
    in_range = regime == "RANGE"
    b1_short = (c15 >= bb_u) | ((c15 - vwap) >= VWAP_ATR_MULT * atr15)
    b2_short = rsi7 >= 78.0
    b3_short = (close5 < open5) & (close5 < prev5)
    q_b_short = b1_short.astype(int) + b2_short.astype(int) + b3_short.astype(int)

    b1_long = (c15 <= bb_l) | ((vwap - c15) >= VWAP_ATR_MULT * atr15)
    b2_long = rsi7 <= 22.0
    b3_long = (close5 > open5) & (close5 > prev5)
    q_b_long = b1_long.astype(int) + b2_long.astype(int) + b3_long.astype(int)

    b_long = in_range & (q_b_long >= B_QUALITY_MIN)
    b_short = in_range & (q_b_short >= B_QUALITY_MIN)

    bias_a = np.zeros(n, dtype=int)
    bias_a = np.where(a_long & ~a_short, 1, bias_a)
    bias_a = np.where(a_short & ~a_long, -1, bias_a)
    # 多空同时满足 → 不做
    q_a = np.where(bias_a > 0, q_a_long, np.where(bias_a < 0, q_a_short, 0))

    bias_b = np.zeros(n, dtype=int)
    bias_b = np.where(b_long & ~b_short, 1, bias_b)
    bias_b = np.where(b_short & ~b_long, -1, bias_b)
    q_b = np.where(bias_b > 0, q_b_long, np.where(bias_b < 0, q_b_short, 0))

    # A、B 同时给出非零方向 → 该时刻不做
    bias = merge_ab_bias(bias_a, bias_b)
    setup = np.full(n, "", dtype=object)
    quality = np.zeros(n, dtype=int)
    use_a = bias == bias_a
    use_a = use_a & (bias != 0)
    use_b = (bias == bias_b) & (bias != 0) & ~use_a
    setup = np.where(use_a, "TREND_PULLBACK", setup)
    setup = np.where(use_b, "RANGE_FADE", setup)
    quality = np.where(use_a, q_a, quality)
    quality = np.where(use_b, q_b, quality)

    bias = np.where(vol_ok, bias, 0)
    setup = np.where(bias == 0, "", setup)
    quality = np.where(bias == 0, 0, quality)

    out = feat.copy()
    out["symbol"] = symbol
    out["regime"] = regime
    out["setup"] = setup
    out["bias"] = bias
    out["quality"] = quality
    out["vol_ok"] = vol_ok
    out["in_funding"] = in_funding_window(idx)
    out["atr15_pct"] = atr15_pct
    return out


def evaluate_base_long(feat: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """每 30 分钟整点/半点无条件做多；同一资金费过滤；无持仓门控在回测层。"""
    out = feat.copy()
    idx = feat.index
    minutes = idx.tz_convert("UTC").minute if idx.tz is not None else idx.minute
    on_grid = (minutes == 0) | (minutes == 30)
    fund = in_funding_window(idx)
    bias = np.where(on_grid & ~fund, 1, 0)
    out["symbol"] = symbol
    out["regime"] = "BASE"
    out["setup"] = np.where(bias != 0, "BASE_LONG", "")
    out["bias"] = bias
    out["quality"] = np.where(bias != 0, 1, 0)
    out["in_funding"] = fund
    return out


def evaluate_base_mom(feat: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """5m close > 前一根 5m close 则做多，否则做空。"""
    out = feat.copy()
    idx = feat.index
    fund = in_funding_window(idx)
    close5 = feat["close"].to_numpy()
    prev5 = feat["prev_close"].to_numpy()
    bias = np.where(close5 > prev5, 1, -1)
    bias = np.where(np.isfinite(prev5) & ~fund, bias, 0)
    out["symbol"] = symbol
    out["regime"] = "BASE"
    out["setup"] = np.where(bias != 0, "BASE_MOM", "")
    out["bias"] = bias
    out["quality"] = np.where(bias != 0, 1, 0)
    out["in_funding"] = fund
    return out


def evaluate_base_1h(feat: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """1h close > EMA20 做多，否则做空。"""
    out = feat.copy()
    idx = feat.index
    fund = in_funding_window(idx)
    c1h = feat["h1_close"].to_numpy()
    ema20 = feat["h1_ema20"].to_numpy()
    bias = np.where(c1h > ema20, 1, -1)
    bias = np.where(np.isfinite(c1h) & np.isfinite(ema20) & ~fund, bias, 0)
    out["symbol"] = symbol
    out["regime"] = "BASE"
    out["setup"] = np.where(bias != 0, "BASE_1H", "")
    out["bias"] = bias
    out["quality"] = np.where(bias != 0, 1, 0)
    out["in_funding"] = fund
    return out


def evaluate_rsi7_ema8(feat: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    简化规则（与 RFA-30 独立，不改原策略）：
    15m RSI(7) + 5m EMA8 交叉。阈值分别取自原文 B2 / A3，达到即开。
    做多：RSI(7) <= 22 且 5m_close > EMA8 且 prev_5m_close <= EMA8
    做空：RSI(7) >= 78 且 5m_close < EMA8 且 prev_5m_close >= EMA8
    多空同时满足则空仓。仍过滤资金费窗口；持仓门控在回测层。
    """
    out = feat.copy()
    idx = feat.index
    fund = in_funding_window(idx)
    rsi7 = feat["m15_rsi7"].to_numpy()
    close5 = feat["close"].to_numpy()
    prev5 = feat["prev_close"].to_numpy()
    ema8 = feat["ema8_5"].to_numpy()
    long_ok = (rsi7 <= RSI7_OVERSOLD) & (close5 > ema8) & (prev5 <= ema8)
    short_ok = (rsi7 >= RSI7_OVERBOUGHT) & (close5 < ema8) & (prev5 >= ema8)
    bias = np.zeros(len(feat), dtype=int)
    bias = np.where(long_ok & ~short_ok, 1, bias)
    bias = np.where(short_ok & ~long_ok, -1, bias)
    ok = np.isfinite(rsi7) & np.isfinite(ema8) & np.isfinite(prev5) & ~fund
    bias = np.where(ok, bias, 0)
    out["symbol"] = symbol
    out["regime"] = "SIMPLE"
    out["setup"] = np.where(bias != 0, "RSI7_EMA8", "")
    out["bias"] = bias
    out["quality"] = np.where(bias != 0, 1, 0)
    out["in_funding"] = fund
    return out


def evaluate_rsi_bb(
    feat: pd.DataFrame,
    symbol: str,
    rsi_period: int | None = None,
    rsi_os: float | None = None,
    rsi_ob: float | None = None,
    bb_k: float | None = None,
    ignore_funding: bool = True,
) -> pd.DataFrame:
    """
    简化规则：15m RSI + 15m 布林轨，两条件同时满足才开。
    做多：RSI <= os 且 close <= 下轨
    做空：RSI >= ob 且 close >= 上轨
    布林中轨为 SMA(20)；上/下轨 = 中轨 ± k * 标准差(ddof=0)。
    默认参数为 IS 冻结值；搜索时传入网格参数。
    """
    rsi_period = RSI_BB_PERIOD if rsi_period is None else rsi_period
    rsi_os = RSI_BB_OS if rsi_os is None else rsi_os
    rsi_ob = RSI_BB_OB if rsi_ob is None else rsi_ob
    bb_k = RSI_BB_K if bb_k is None else bb_k
    out = feat.copy()
    idx = feat.index
    fund = np.zeros(len(idx), dtype=bool) if ignore_funding else in_funding_window(idx)
    rsi_col = "m15_rsi7" if int(rsi_period) == 7 else "m15_rsi14"
    rsi = feat[rsi_col].to_numpy()
    mid = feat["m15_bb_mid"].to_numpy()
    std = (feat["m15_bb_upper"].to_numpy() - mid) / 2.0
    close15 = feat["m15_close"].to_numpy()
    upper = mid + bb_k * std
    lower = mid - bb_k * std
    long_ok = (rsi <= rsi_os) & (close15 <= lower)
    short_ok = (rsi >= rsi_ob) & (close15 >= upper)
    bias = np.zeros(len(feat), dtype=int)
    bias = np.where(long_ok & ~short_ok, 1, bias)
    bias = np.where(short_ok & ~long_ok, -1, bias)
    ok = np.isfinite(rsi) & np.isfinite(lower) & np.isfinite(upper) & np.isfinite(close15) & ~fund
    bias = np.where(ok, bias, 0)
    out["symbol"] = symbol
    out["regime"] = "SIMPLE"
    out["setup"] = np.where(bias != 0, "RSI_BB", "")
    out["bias"] = bias
    out["quality"] = np.where(bias != 0, 1, 0)
    out["in_funding"] = fund
    out["rsi_bb_period"] = rsi_period
    out["rsi_bb_os"] = rsi_os
    out["rsi_bb_ob"] = rsi_ob
    out["rsi_bb_k"] = bb_k
    out["rsi"] = rsi
    out["bb_mid"] = mid
    out["bb_upper_k"] = upper
    out["bb_lower_k"] = lower
    return out


def apply_position_gate(bias: np.ndarray, index: pd.DatetimeIndex, hold_minutes: int = HOLD_MINUTES) -> np.ndarray:
    """同一标的同时最多 1 笔；开仓间隔 >= 30 分钟；持仓未到期不做。"""
    n = len(bias)
    taken = np.zeros(n, dtype=bool)
    expire = None
    gap = pd.Timedelta(minutes=hold_minutes)
    for i in range(n):
        t = index[i]
        if expire is not None and t < expire:
            continue
        if bias[i] == 0:
            continue
        taken[i] = True
        expire = t + gap
    return taken


def settle_trades(
    signals: pd.DataFrame,
    close_1m: pd.Series,
    taken: np.ndarray,
) -> pd.DataFrame:
    """
    入场价 = 该 5m close；结算价 = T+30 分钟那根 1m（close_time 对齐）的 close。
    缺 K 则作废不计入。
    """
    out = signals.copy()
    n = len(out)
    idx = out.index
    settle_time = idx + pd.Timedelta(minutes=HOLD_MINUTES)
    settle_arr = close_1m.reindex(settle_time).to_numpy()
    entry = out["close"].to_numpy()
    bias = out["bias"].to_numpy()

    entry_out = np.full(n, np.nan)
    settle_out = np.full(n, np.nan)
    pnl = np.full(n, np.nan)
    win = np.full(n, np.nan)
    void = np.zeros(n, dtype=bool)

    ok = taken & np.isfinite(settle_arr)
    void_taken = taken & ~np.isfinite(settle_arr)
    entry_out[taken] = entry[taken]
    settle_out[ok] = settle_arr[ok]
    void[void_taken] = True

    s = settle_arr
    e = entry
    b = bias
    win_mask = ((b > 0) & (s > e)) | ((b < 0) & (s < e))
    lose_mask = ((b > 0) & (s < e)) | ((b < 0) & (s > e))
    tie_mask = np.isfinite(s) & np.isfinite(e) & (s == e)
    pnl_all = np.where(win_mask, WIN_PAYOFF, np.where(lose_mask, LOSS_PAYOFF, np.where(tie_mask, 0.0, np.nan)))
    pnl[ok] = pnl_all[ok]
    win[ok] = (pnl[ok] > 0).astype(float)

    taken_s = pd.Series(taken, index=out.index)
    settle_s = pd.Series(settle_time, index=out.index)
    settle_s.loc[~taken_s] = pd.NaT
    out["entry"] = entry_out
    out["settle_time"] = settle_s
    out["settle"] = settle_out
    out["pnl"] = pnl
    out["win"] = win
    out["void"] = void
    out["traded"] = taken
    return out


def extract_trades(signals: pd.DataFrame) -> pd.DataFrame:
    """实际尝试开仓的行（含作废）。有效成交为 traded 且非 void。"""
    t = signals.loc[signals["traded"]].copy()
    t = t.reset_index().rename(columns={"close_time": "timestamp", "index": "timestamp"})
    if "timestamp" not in t.columns:
        t = t.rename(columns={t.columns[0]: "timestamp"})
    cols = [
        "timestamp",
        "symbol",
        "regime",
        "setup",
        "bias",
        "quality",
        "entry",
        "settle_time",
        "settle",
        "pnl",
        "win",
        "void",
        "close",
        "open",
        "prev_close",
        "ema8_5",
        "atr14_5",
        "tr5",
        "m15_close",
        "m15_ema20",
        "m15_ema50",
        "m15_atr14",
        "m15_rsi14",
        "m15_rsi7",
        "m15_bb_upper",
        "m15_bb_lower",
        "m15_bb_mid",
        "rsi",
        "bb_mid",
        "bb_upper_k",
        "bb_lower_k",
        "vwap",
        "h1_close",
        "h1_ema20",
        "h1_ema50",
        "h1_adx",
        "atr15_pct",
        "in_funding",
        "vol_ok",
    ]
    have = [c for c in cols if c in t.columns]
    return t[have]
