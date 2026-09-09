"""
向量化技术指标。所有计算只使用截至当前时刻已发生的 OHLCV。
周期合成：1m → 5m/15m/1h，label=right（收盘才知道该根 K）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]


def to_close_index(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Binance open_time 为开盘时刻；内部统一用收盘时刻做索引。"""
    out = df_1m.copy()
    if "open_time" not in out.columns:
        raise ValueError("需要 open_time 列")
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True)
    out["close_time"] = out["open_time"] + pd.Timedelta(minutes=1)
    out = out.set_index("close_time").sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def resample_ohlcv(df_1m_close: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    1m（close_time 索引）合成更高周期，label=right, closed=right。
    例：close_time 00:01..00:05 → 5m 标签 00:05。
    """
    agg = (
        df_1m_close[OHLCV]
        .resample(rule, label="right", closed="right", origin="epoch")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
    )
    return agg.dropna(subset=["open", "close"])


def wilder_rma(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder 平滑：前 period 个有效值取 SMA，之后 RMA = (prev*(n-1)+x)/n。"""
    x = np.asarray(values, dtype=np.float64)
    n = x.size
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out
    finite = np.isfinite(x)
    i = 0
    while i < n and not finite[i]:
        i += 1
    end = i + period - 1
    if end >= n or not np.all(finite[i : end + 1]):
        # 序列中有空洞时走慢路径
        buf: list[float] = []
        prev = np.nan
        for k in range(n):
            if not finite[k]:
                out[k] = np.nan
                continue
            if len(buf) < period:
                buf.append(float(x[k]))
                if len(buf) == period:
                    prev = float(np.mean(buf))
                    out[k] = prev
                continue
            prev = (prev * (period - 1) + x[k]) / period
            out[k] = prev
        return out
    out[end] = x[i : end + 1].mean()
    rest = x[end:].copy()
    rest[0] = out[end]
    out[end:] = pd.Series(rest).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    return out


def ema(values: np.ndarray | pd.Series, span: int) -> np.ndarray:
    """标准 EMA，alpha=2/(span+1)，adjust=False。"""
    s = pd.Series(np.asarray(values, dtype=np.float64))
    return s.ewm(span=span, adjust=False, min_periods=span).mean().to_numpy()


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan
    h_l = high - low
    h_pc = np.abs(high - prev_close)
    l_pc = np.abs(low - prev_close)
    return np.nanmax(np.vstack([h_l, h_pc, l_pc]), axis=0)


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    return wilder_rma(true_range(high, low, close), period)


def rsi_wilder(close: np.ndarray, period: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Wilder RSI。返回 (rsi, avg_gain, avg_loss)。
    第一个有效值在 index=period（用掉 period 个 delta）。
    """
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    rsi = np.full(n, np.nan)
    avg_gain = np.full(n, np.nan)
    avg_loss = np.full(n, np.nan)
    if n < period + 1:
        return rsi, avg_gain, avg_loss
    deltas = np.diff(c)
    gains = np.clip(deltas, 0.0, None)
    losses = np.clip(-deltas, 0.0, None)
    # gains[0] 对应 close[1]；SMA 种子落在 close[period]
    seed_g = wilder_rma(gains, period)
    seed_l = wilder_rma(losses, period)
    avg_gain[1:] = seed_g
    avg_loss[1:] = seed_l
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100.0 - 100.0 / (1.0 + rs)
        zero_loss = avg_loss == 0
        rsi = np.where(zero_loss & (avg_gain == 0), 50.0, rsi)
        rsi = np.where(zero_loss & (avg_gain > 0), 100.0, rsi)
        rsi = np.where(~np.isfinite(avg_gain), np.nan, rsi)
    return rsi, avg_gain, avg_loss


def bollinger(close: np.ndarray, period: int = 20, n_std: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """SMA ± n_std * 样本标准差（ddof=0，与常见交易软件一致）。"""
    s = pd.Series(np.asarray(close, dtype=np.float64))
    mid = s.rolling(period, min_periods=period).mean()
    std = s.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + n_std * std
    lower = mid - n_std * std
    return lower.to_numpy(), mid.to_numpy(), upper.to_numpy()


def rolling_sum_sumsq(close: np.ndarray, period: int = 20) -> tuple[np.ndarray, np.ndarray]:
    s = pd.Series(np.asarray(close, dtype=np.float64))
    return s.rolling(period).sum().to_numpy(), (s.pow(2).rolling(period).sum()).to_numpy()


def adx_wilder(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14
) -> dict[str, np.ndarray]:
    """Wilder ADX，含 +DI/-DI 及中间平滑项，便于形成中 K 做一步更新。"""
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = h.size
    up = np.empty(n)
    down = np.empty(n)
    up[0] = np.nan
    down[0] = np.nan
    up[1:] = h[1:] - h[:-1]
    down[1:] = l[:-1] - l[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm[0] = np.nan
    minus_dm[0] = np.nan
    tr = true_range(h, l, c)
    sm_plus = wilder_rma(plus_dm, period)
    sm_minus = wilder_rma(minus_dm, period)
    sm_tr = wilder_rma(tr, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * sm_plus / sm_tr
        minus_di = 100.0 * sm_minus / sm_tr
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = wilder_rma(dx, period)
    return {
        "plus_dm": plus_dm,
        "minus_dm": minus_dm,
        "tr": tr,
        "sm_plus_dm": sm_plus,
        "sm_minus_dm": sm_minus,
        "atr": sm_tr,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "dx": dx,
        "adx": adx,
    }


def vwap_1m(df_1m_close: pd.DataFrame) -> pd.Series:
    """
    UTC 00:00 重置。典型价 tp=(H+L+C)/3。
    交易日按 1m 的 open_time 归属（00:00 开盘的 K 起算新一天）。
    """
    tp = (df_1m_close["high"] + df_1m_close["low"] + df_1m_close["close"]) / 3.0
    tpv = tp * df_1m_close["volume"]
    day = pd.to_datetime(df_1m_close["open_time"], utc=True).dt.floor("D")
    if not isinstance(day, pd.Series):
        day = pd.Series(day, index=df_1m_close.index)
    vol = df_1m_close["volume"]
    cum_tpv = tpv.groupby(day.values).cumsum()
    cum_vol = vol.groupby(day.values).cumsum()
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = cum_tpv / cum_vol.replace(0, np.nan)
    vwap.name = "vwap"
    return vwap


def enrich_completed_bars(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """在已收盘的 5m/15m/1h 上计算指标（向量化 + Wilder 一次扫描）。"""
    out = df.copy()
    h = out["high"].to_numpy()
    l = out["low"].to_numpy()
    c = out["close"].to_numpy()
    out["ema8"] = ema(c, 8)
    out["ema20"] = ema(c, 20)
    out["ema50"] = ema(c, 50)
    out["atr14"] = atr_wilder(h, l, c, 14)
    out["tr"] = true_range(h, l, c)
    rsi14, ag14, al14 = rsi_wilder(c, 14)
    rsi7, ag7, al7 = rsi_wilder(c, 7)
    out["rsi14"] = rsi14
    out["rsi7"] = rsi7
    out["avg_gain14"] = ag14
    out["avg_loss14"] = al14
    out["avg_gain7"] = ag7
    out["avg_loss7"] = al7
    lo, mid, up = bollinger(c, 20, 2.0)
    out["bb_lower"] = lo
    out["bb_mid"] = mid
    out["bb_upper"] = up
    sum20, sumsq20 = rolling_sum_sumsq(c, 20)
    out["sum20"] = sum20
    out["sumsq20"] = sumsq20
    out["close_shift19"] = out["close"].shift(19)
    if kind == "1h":
        adx = adx_wilder(h, l, c, 14)
        for k, v in adx.items():
            out[k] = v
    return out


def forming_ohlcv(df_5m: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    在每根 5m 收盘，构造当前形成中的 freq 周期 OHLCV。
    若该时刻恰好是 freq 收盘，则为完整 K。
    """
    end = df_5m.index.ceil(freq)
    g = df_5m.groupby(end)
    return pd.DataFrame(
        {
            "open": g["open"].transform("first"),
            "high": g["high"].cummax(),
            "low": g["low"].cummin(),
            "close": df_5m["close"],
            "volume": g["volume"].cumsum(),
        },
        index=df_5m.index,
    )


def _ffill_htf(df_5m: pd.DataFrame, df_htf: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    mapped = df_htf[cols].reindex(df_5m.index)
    return mapped.ffill()


def attach_forming_indicators(df_5m: pd.DataFrame, df_htf: pd.DataFrame, freq: str, prefix: str) -> pd.DataFrame:
    """
    把更高周期指标对齐到 5m：已收盘用完整值；未收盘用「上一根完整状态 + 当前形成中 K」一步更新。
    不得把尚未发生的 1m 填进当前 K。
    """
    forming = forming_ohlcv(df_5m, freq)
    is_close = df_5m.index.isin(df_htf.index)
    # 用 ffill 取「截至该 5m 已收盘」的高周期状态，避免 floor 后出现重复标签
    state_cols = [
        "open",
        "high",
        "low",
        "close",
        "ema8",
        "ema20",
        "ema50",
        "atr14",
        "rsi14",
        "rsi7",
        "avg_gain14",
        "avg_loss14",
        "avg_gain7",
        "avg_loss7",
        "bb_lower",
        "bb_mid",
        "bb_upper",
        "sum20",
        "sumsq20",
        "close_shift19",
        "tr",
    ]
    extra_1h = ["adx", "plus_di", "minus_di", "sm_plus_dm", "sm_minus_dm", "dx"]
    cols = [c for c in state_cols if c in df_htf.columns]
    if prefix == "h1":
        cols = cols + [c for c in extra_1h if c in df_htf.columns]
    st = df_htf[cols].reindex(df_5m.index, method="ffill")

    out = pd.DataFrame(index=df_5m.index)
    out[f"{prefix}_open"] = np.where(is_close, st["open"], forming["open"])
    out[f"{prefix}_high"] = np.where(is_close, st["high"], forming["high"])
    out[f"{prefix}_low"] = np.where(is_close, st["low"], forming["low"])
    out[f"{prefix}_close"] = np.where(is_close, st["close"], forming["close"])

    alpha = lambda span: 2.0 / (span + 1.0)
    for span, name in ((8, "ema8"), (20, "ema20"), (50, "ema50")):
        a = alpha(span)
        step = a * forming["close"].to_numpy() + (1.0 - a) * st[name].to_numpy()
        out[f"{prefix}_{name}"] = np.where(is_close, st[name], step)

    # ATR 一步：TR(形成中 K vs 上一根完整收盘)
    prev_c = st["close"].to_numpy()
    fh, fl, fc = forming["high"].to_numpy(), forming["low"].to_numpy(), forming["close"].to_numpy()
    tr_form = np.nanmax(
        np.vstack([fh - fl, np.abs(fh - prev_c), np.abs(fl - prev_c)]),
        axis=0,
    )
    atr_step = (st["atr14"].to_numpy() * 13.0 + tr_form) / 14.0
    out[f"{prefix}_atr14"] = np.where(is_close, st["atr14"], atr_step)
    tr_closed = st["tr"].to_numpy() if "tr" in st.columns else tr_form
    out[f"{prefix}_tr"] = np.where(is_close, tr_closed, tr_form)

    def _rsi_step(period: int, ag_col: str, al_col: str) -> np.ndarray:
        delta = fc - prev_c
        gain = np.clip(delta, 0.0, None)
        loss = np.clip(-delta, 0.0, None)
        ag = (st[ag_col].to_numpy() * (period - 1) + gain) / period
        al = (st[al_col].to_numpy() * (period - 1) + loss) / period
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = ag / al
            r = 100.0 - 100.0 / (1.0 + rs)
            r = np.where((al == 0) & (ag == 0), 50.0, r)
            r = np.where((al == 0) & (ag > 0), 100.0, r)
        return ag, al, r

    ag14, al14, r14 = _rsi_step(14, "avg_gain14", "avg_loss14")
    ag7, al7, r7 = _rsi_step(7, "avg_gain7", "avg_loss7")
    out[f"{prefix}_rsi14"] = np.where(is_close, st["rsi14"], r14)
    out[f"{prefix}_rsi7"] = np.where(is_close, st["rsi7"], r7)

    # 布林：最近 19 根已收盘 + 形成中收盘；ddof=0
    oldest = st["close_shift19"].to_numpy()
    new_sum = st["sum20"].to_numpy() - oldest + fc
    new_sumsq = st["sumsq20"].to_numpy() - oldest**2 + fc**2
    sma = new_sum / 20.0
    var = np.maximum(new_sumsq / 20.0 - sma**2, 0.0)
    std = np.sqrt(var)
    bb_l, bb_m, bb_u = sma - 2.0 * std, sma, sma + 2.0 * std
    out[f"{prefix}_bb_lower"] = np.where(is_close, st["bb_lower"], bb_l)
    out[f"{prefix}_bb_mid"] = np.where(is_close, st["bb_mid"], bb_m)
    out[f"{prefix}_bb_upper"] = np.where(is_close, st["bb_upper"], bb_u)

    if prefix == "h1" and "adx" in st.columns:
        prev_h = st["high"].to_numpy()
        prev_l = st["low"].to_numpy()
        up_move = fh - prev_h
        down_move = prev_l - fl
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        sm_plus = (st["sm_plus_dm"].to_numpy() * 13.0 + plus_dm) / 14.0
        sm_minus = (st["sm_minus_dm"].to_numpy() * 13.0 + minus_dm) / 14.0
        atr = atr_step
        with np.errstate(divide="ignore", invalid="ignore"):
            plus_di = 100.0 * sm_plus / atr
            minus_di = 100.0 * sm_minus / atr
            dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx_step = (st["adx"].to_numpy() * 13.0 + dx) / 14.0
        out[f"{prefix}_adx"] = np.where(is_close, st["adx"], adx_step)
        out[f"{prefix}_plus_di"] = np.where(is_close, st["plus_di"], plus_di)
        out[f"{prefix}_minus_di"] = np.where(is_close, st["minus_di"], minus_di)
    return out


def build_feature_frame(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    从 1m 生成每根 5m 收盘可用的全部特征。时间向前推进，形成中的 15m/1h 只用当前及之前的 1m。
    """
    d1 = to_close_index(df_1m)
    d5 = enrich_completed_bars(resample_ohlcv(d1, "5min"), "5m")
    d15 = enrich_completed_bars(resample_ohlcv(d1, "15min"), "15m")
    d1h = enrich_completed_bars(resample_ohlcv(d1, "1h"), "1h")
    vwap = vwap_1m(d1)

    feat = pd.DataFrame(index=d5.index)
    feat["open"] = d5["open"]
    feat["high"] = d5["high"]
    feat["low"] = d5["low"]
    feat["close"] = d5["close"]
    feat["volume"] = d5["volume"]
    feat["prev_close"] = d5["close"].shift(1)
    feat["ema8_5"] = d5["ema8"]
    feat["atr14_5"] = d5["atr14"]
    feat["tr5"] = d5["tr"]
    feat["vwap"] = vwap.reindex(d5.index)

    f15 = attach_forming_indicators(d5, d15, "15min", "m15")
    f1h = attach_forming_indicators(d5, d1h, "1h", "h1")
    feat = feat.join(f15).join(f1h)

    # 预热：各周期至少 200 根已收盘
    n5 = np.arange(len(d5)) + 1
    n15 = d15["close"].notna().cumsum().reindex(d5.index).ffill().fillna(0)
    n1h = d1h["close"].notna().cumsum().reindex(d5.index).ffill().fillna(0)
    feat["bars_5"] = n5
    feat["bars_15"] = n15.to_numpy()
    feat["bars_1h"] = n1h.to_numpy()
    feat["warm"] = (feat["bars_5"] >= 200) & (feat["bars_15"] >= 200) & (feat["bars_1h"] >= 200) & feat["h1_adx"].notna()
    feat.index.name = "timestamp"
    return feat
