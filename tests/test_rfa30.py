"""RFA-30 单元测试：指标、无未来函数、资金费窗口、A/B 冲突、结算对齐。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import build_feature_frame, rsi_wilder, to_close_index
from strategy import (
    HOLD_MINUTES,
    WIN_PAYOFF,
    apply_position_gate,
    evaluate_rfa30,
    evaluate_rsi7_ema8,
    evaluate_rsi_bb,
    in_funding_window,
    merge_ab_bias,
    settle_trades,
)


def test_wilder_rsi_matches_hand_calc():
    """Wilder RSI(3) 与手算短序列一致。"""
    close = np.array([1.0, 2.0, 3.0, 2.0, 5.0, 4.0])
    rsi, avg_gain, avg_loss = rsi_wilder(close, period=3)
    # index=3：三个 delta [+1,+1,-1] → avg_gain=2/3, avg_loss=1/3, RSI=66.666...
    assert rsi[3] == pytest.approx(100.0 - 100.0 / 3.0, rel=1e-12)
    assert avg_gain[3] == pytest.approx(2.0 / 3.0, rel=1e-12)
    assert avg_loss[3] == pytest.approx(1.0 / 3.0, rel=1e-12)
    # index=4：RMA 更新
    assert avg_gain[4] == pytest.approx(13.0 / 9.0, rel=1e-12)
    assert avg_loss[4] == pytest.approx(2.0 / 9.0, rel=1e-12)
    assert rsi[4] == pytest.approx(100.0 - 100.0 / 7.5, rel=1e-12)
    # index=5
    assert avg_gain[5] == pytest.approx(26.0 / 27.0, rel=1e-12)
    assert avg_loss[5] == pytest.approx(13.0 / 27.0, rel=1e-12)
    assert rsi[5] == pytest.approx(100.0 - 100.0 / 3.0, rel=1e-12)
    assert np.isnan(rsi[2])


def test_funding_window_half_open():
    """[结算-15min, 结算+10min) ；00:10 / 08:10 不在窗口内。"""
    times = pd.to_datetime(
        [
            "2024-01-01 23:45:00",
            "2024-01-01 23:55:00",
            "2024-01-02 00:00:00",
            "2024-01-02 00:05:00",
            "2024-01-02 00:10:00",
            "2024-01-02 07:45:00",
            "2024-01-02 08:09:00",
            "2024-01-02 08:10:00",
            "2024-01-02 15:45:00",
            "2024-01-02 16:09:00",
            "2024-01-02 16:10:00",
            "2024-01-02 12:00:00",
        ],
        utc=True,
    )
    got = in_funding_window(pd.DatetimeIndex(times))
    expect = np.array(
        [True, True, True, True, False, True, True, False, True, True, False, False]
    )
    assert np.array_equal(got, expect)


def test_ab_conflict_is_flat():
    """A、B 同时非零方向 → 空仓。"""
    a = np.array([1, 1, -1, 0, 0])
    b = np.array([1, -1, 0, -1, 0])
    merged = merge_ab_bias(a, b)
    assert merged.tolist() == [0, 0, -1, -1, 0]


def test_long_short_conflict_flat_in_merge():
    """多空同时满足在信号层已置 0，再与对向 setup 冲突仍为空。"""
    a = np.array([0, 0])  # 多空同时满足后 bias_a=0
    b = np.array([1, -1])
    assert merge_ab_bias(a, b).tolist() == [1, -1]


def _one_minute_df(start: str, n: int, close: float | None = None) -> pd.DataFrame:
    open_time = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    px = np.full(n, 100.0 if close is None else close, dtype=float)
    # 让价格随时间轻微上行，便于区分不同分钟
    px = px + np.arange(n) * 0.01
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": px,
            "high": px + 0.05,
            "low": px - 0.05,
            "close": px,
            "volume": np.full(n, 10.0),
        }
    )


def test_settlement_aligns_t_plus_30m_1m_close():
    """入场 5m close 时刻 T，结算为 close_time = T+30min 的 1m close。"""
    df = _one_minute_df("2024-03-01 00:00:00", 90)
    d1 = to_close_index(df)
    # 5m 标签 00:05 对应 1m close_time 00:05
    t = pd.Timestamp("2024-03-01 00:05:00", tz="UTC")
    entry_px = float(d1.loc[t, "close"])
    sig = pd.DataFrame(
        {"close": [entry_px], "bias": [1]},
        index=pd.DatetimeIndex([t]),
    )
    taken = np.array([True])
    filled = settle_trades(sig, d1["close"], taken)
    settle_t = pd.Timestamp("2024-03-01 00:35:00", tz="UTC")
    assert filled["settle_time"].iloc[0] == settle_t
    assert filled["settle"].iloc[0] == pytest.approx(float(d1.loc[settle_t, "close"]))
    assert filled["entry"].iloc[0] == pytest.approx(entry_px)
    # 价格随分钟递增，多头应获胜
    assert filled["pnl"].iloc[0] == pytest.approx(WIN_PAYOFF)
    assert filled["void"].iloc[0] == False  # noqa: E712


def test_missing_settle_bar_is_void():
    """缺结算 1m 则作废不计入。"""
    df = _one_minute_df("2024-03-01 00:00:00", 20)
    d1 = to_close_index(df)
    t = pd.Timestamp("2024-03-01 00:05:00", tz="UTC")
    sig = pd.DataFrame({"close": [100.0], "bias": [1]}, index=pd.DatetimeIndex([t]))
    filled = settle_trades(sig, d1["close"], np.array([True]))
    assert bool(filled["void"].iloc[0]) is True
    assert np.isnan(filled["pnl"].iloc[0])


def _rw_1m(hours: int, seed: int = 1) -> pd.DataFrame:
    n = hours * 60
    rng = np.random.default_rng(seed)
    open_time = pd.date_range("2023-01-01", periods=n, freq="1min", tz="UTC")
    ret = rng.normal(0.0, 0.0004, n)
    close = 20000.0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0, 0.0003, n))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0, 0.0003, n))
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(5, 20, n),
        }
    )


def test_no_lookahead_mutating_future_close():
    """改掉某根 close 不得影响该根之前的 5m 信号。"""
    df = _rw_1m(220, seed=7)
    feat0 = build_feature_frame(df)
    sig0 = evaluate_rfa30(feat0, "BTCUSDT")
    # 预热后某根 1m：open_time = 210h
    cut_open = pd.Timestamp("2023-01-09 18:00:00", tz="UTC")  # 8d+18h = 210h
    assert (df["open_time"] == cut_open).any()
    df2 = df.copy()
    df2.loc[df2["open_time"] == cut_open, "close"] *= 1.05
    df2.loc[df2["open_time"] == cut_open, "high"] = np.maximum(
        df2.loc[df2["open_time"] == cut_open, "high"],
        df2.loc[df2["open_time"] == cut_open, "close"],
    )
    feat1 = build_feature_frame(df2)
    sig1 = evaluate_rfa30(feat1, "BTCUSDT")
    cut_close = cut_open + pd.Timedelta(minutes=1)
    cols = ["regime", "setup", "bias", "quality", "close", "h1_adx", "m15_rsi14", "vwap"]
    a = sig0.loc[sig0.index < cut_close, cols]
    b = sig1.loc[sig1.index < cut_close, cols]
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=0, atol=1e-12)


def test_position_gate_min_gap_30m():
    idx = pd.date_range("2024-01-01", periods=12, freq="5min", tz="UTC")
    bias = np.ones(12, dtype=int)
    taken = apply_position_gate(bias, idx, hold_minutes=HOLD_MINUTES)
    # 00:00 开，00:30 到期可再开
    times = list(idx[taken])
    assert times[0] == idx[0]
    assert times[1] == idx[0] + pd.Timedelta(minutes=30)
    diffs = np.diff(idx[taken]).astype("timedelta64[m]").astype(int)
    assert np.all(diffs >= 30)


def test_rsi7_ema8_opens_on_threshold():
    """RSI(7)<=22 且 5m 上穿 EMA8 做多；>=78 且下穿 EMA8 做空。"""
    idx = pd.DatetimeIndex(
        [
            "2024-01-02 12:00:00",
            "2024-01-02 12:05:00",
            "2024-01-02 12:10:00",
            "2024-01-02 12:15:00",
        ],
        tz="UTC",
    )
    feat = pd.DataFrame(
        {
            "m15_rsi7": [22.0, 78.0, 22.0, 20.0],
            "close": [101.0, 99.0, 101.0, 101.0],
            "prev_close": [99.0, 101.0, 102.0, 100.0],
            "ema8_5": [100.0, 100.0, 100.0, 100.0],
        },
        index=idx,
    )
    out = evaluate_rsi7_ema8(feat, "BTCUSDT")
    # 1: RSI=22 上穿 EMA8 → 多；2: RSI=78 下穿 → 空；3: RSI 超卖但未交叉 → 0；4: 上穿且 RSI=20 → 多
    assert out["bias"].tolist() == [1, -1, 0, 1]
    fund_idx = pd.DatetimeIndex(["2024-01-02 08:00:00"], tz="UTC")
    feat2 = pd.DataFrame(
        {"m15_rsi7": [20.0], "close": [101.0], "prev_close": [99.0], "ema8_5": [100.0]},
        index=fund_idx,
    )
    assert evaluate_rsi7_ema8(feat2, "BTCUSDT")["bias"].iloc[0] == 0


def test_rsi_bb_requires_both_indicators():
    """RSI 触及阈值但未到布林轨则不开；两条件同时满足才开。"""
    idx = pd.DatetimeIndex(
        ["2024-01-02 12:00:00", "2024-01-02 12:05:00", "2024-01-02 12:10:00"],
        tz="UTC",
    )
    # 存储轨为 k=2：mid=100, upper=110 → std=5；冻结 k=2.5 → 下轨 87.5、上轨 112.5
    feat = pd.DataFrame(
        {
            "m15_rsi7": [22.0, 22.0, 78.0],
            "m15_close": [87.0, 90.0, 113.0],
            "m15_bb_mid": [100.0, 100.0, 100.0],
            "m15_bb_upper": [110.0, 110.0, 110.0],
        },
        index=idx,
    )
    out = evaluate_rsi_bb(feat, "BTCUSDT", rsi_period=7, rsi_os=22, rsi_ob=78, bb_k=2.5)
    assert out["bias"].tolist() == [1, 0, -1]


def test_detect_is_aligns_replay_constants():
    """有本地 1m 时，检测脚本 IS 必须打到冻结回放的 N/胜率。"""
    from pathlib import Path as P

    from detect import ALIGN_IS_N, check_align, scan_all

    if not (P("data/BTCUSDT/1m/2023-01.parquet")).exists():
        pytest.skip("无月度 1m 数据")
    trades = scan_all()
    errs = check_align(trades, P("output/trades.csv"))
    assert errs == [], errs
    from detect import is_stats

    st = is_stats(trades)
    assert st["N"] == ALIGN_IS_N
