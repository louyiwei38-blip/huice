from __future__ import annotations

import math
from statistics import median

from .config import Config
from .models import Bar, HvnZone, Structure


def compute_structure(closed: list[Bar], cfg: Config) -> Structure | None:
    if len(closed) < max(cfg.lookback, cfg.atr_period + 2, cfg.volume_near + 2):
        return None
    window = closed[-cfg.lookback :]
    box = _box(window, cfg)
    if box is None:
        return None
    range_high, range_low = box
    height = range_high - range_low
    if height <= 0:
        return None
    mid = (range_high + range_low) / 2.0
    atr = wilder_atr(closed, cfg.atr_period)
    if atr is None or atr <= 0:
        return None
    last = window[-1]
    touch_tol = max(cfg.touch_pct * last.close, cfg.touch_atr_mult * atr)
    poc, vah, val, hvn_zones = volume_profile(window, range_high, range_low, mid, cfg)
    highs, lows = swing_points(window, cfg, mid, height)
    edge = edge_width(height, last.close, atr, cfg)
    er = kaufman_er(window)
    is_ranging, vol_near, vol_typical = volume_regime(window, cfg)
    return Structure(
        asof_open_time=last.open_time,
        range_high=range_high,
        range_low=range_low,
        range_mid=mid,
        range_height=height,
        upper_band_low=range_high - edge,
        lower_band_high=range_low + edge,
        atr=atr,
        is_ranging=is_ranging,
        vol_near=vol_near,
        vol_typical=vol_typical,
        poc=poc,
        vah=vah,
        val=val,
        hvn_zones=hvn_zones,
        swing_highs=highs,
        swing_lows=lows,
        touch_tol=touch_tol,
        er=er,
    )


def edge_width(height: float, price: float, atr: float, cfg: Config) -> float:
    """箱体上下沿带宽度。默认 20% 箱体高度；过宽时截到价格比例或 ATR 上限。"""
    edge = cfg.edge_frac * height
    wide = cfg.edge_wide_pct
    cap = cfg.max_edge_pct
    if wide is not None and cap is not None and price > 0 and edge / price > wide:
        edge = min(edge, cap * price)
    wide_atr = cfg.edge_wide_atr
    cap_atr = cfg.max_edge_atr
    if wide_atr is not None and cap_atr is not None and atr > 0 and edge / atr > wide_atr:
        edge = min(edge, cap_atr * atr)
    return edge


def compute_prior_box(prior: list[Bar], cfg: Config) -> Structure | None:
    return compute_structure(prior, cfg)


def volume_regime(window: list[Bar], cfg: Config) -> tuple[bool, float, float]:
    vols = [b.volume for b in window]
    typical = median(vols)
    near = sum(vols[-cfg.volume_near :]) / float(cfg.volume_near)
    if typical <= 0:
        return False, near, typical
    return near < cfg.volume_ratio * typical, near, typical


def kaufman_er(window: list[Bar]) -> float:
    if len(window) < 2:
        return 1.0
    change = abs(window[-1].close - window[0].close)
    path = sum(abs(window[i].close - window[i - 1].close) for i in range(1, len(window)))
    if path <= 0:
        return 0.0
    return change / path


def wilder_atr(bars: list[Bar], period: int) -> float | None:
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        prev_c = bars[i - 1].close
        high = bars[i].high
        low = bars[i].low
        tr = max(high - low, abs(high - prev_c), abs(low - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _box(window: list[Bar], cfg: Config) -> tuple[float, float] | None:
    if len(window) < 8:
        return None
    drop = max(0, cfg.drop_extreme_bars)
    highs = sorted(window, key=lambda b: b.high)
    lows = sorted(window, key=lambda b: b.low)
    exclude: set[int] = set()
    if drop:
        exclude.add(id(highs[-1]))
        exclude.add(id(lows[0]))
    remain = [b for b in window if id(b) not in exclude] or list(window)
    return max(b.high for b in remain), min(b.low for b in remain)


def volume_profile(
    window: list[Bar],
    range_high: float,
    range_low: float,
    mid: float,
    cfg: Config,
) -> tuple[float, float, float, list[HvnZone]]:
    n = cfg.vp_buckets
    height = range_high - range_low
    if height <= 0:
        center = (range_high + range_low) / 2.0
        return center, range_high, range_low, []

    edges = [range_low + height * i / n for i in range(n + 1)]
    vol = [0.0] * n
    for bar in window:
        lo = min(bar.low, bar.high)
        hi = max(bar.low, bar.high)
        span = hi - lo
        if span <= 0:
            idx = min(n - 1, max(0, int((bar.close - range_low) / height * n)))
            vol[idx] += bar.volume
            continue
        for i in range(n):
            a, b = edges[i], edges[i + 1]
            overlap = min(hi, b) - max(lo, a)
            if overlap > 0:
                vol[i] += bar.volume * overlap / span

    total = sum(vol)
    if total <= 0:
        center = (range_high + range_low) / 2.0
        return center, range_high, range_low, []

    poc_i = max(range(n), key=lambda i: vol[i])
    poc = 0.5 * (edges[poc_i] + edges[poc_i + 1])

    left = right = poc_i
    covered = vol[poc_i]
    target = total * cfg.value_area_pct
    while covered < target and (left > 0 or right < n - 1):
        left_vol = vol[left - 1] if left > 0 else -1.0
        right_vol = vol[right + 1] if right < n - 1 else -1.0
        if right_vol > left_vol:
            right += 1
            covered += vol[right]
        elif left_vol > right_vol:
            left -= 1
            covered += vol[left]
        elif right < n - 1:
            right += 1
            covered += vol[right]
        else:
            left -= 1
            covered += vol[left]
    val = edges[left]
    vah = edges[right + 1]

    positive = [v for v in vol if v > 0]
    if not positive:
        return poc, vah, val, []
    ranked = sorted(positive)
    thresh_i = min(len(ranked) - 1, max(0, int(math.floor(cfg.hvn_percentile * (len(ranked) - 1)))))
    thresh = ranked[thresh_i]

    hvn: list[HvnZone] = []
    for i, v in enumerate(vol):
        if v < thresh:
            continue
        if i == poc_i:
            continue
        center = 0.5 * (edges[i] + edges[i + 1])
        side = "upper" if center >= mid else "lower"
        hvn.append(HvnZone(low=edges[i], high=edges[i + 1], center=center, side=side))
    return poc, vah, val, hvn


def swing_points(
    window: list[Bar],
    cfg: Config,
    mid: float,
    height: float,
) -> tuple[list[float], list[float]]:
    l = cfg.swing_left
    n = len(window)
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    min_dist = cfg.swing_min_mid_frac * height
    for i in range(l, n - l):
        h = window[i].high
        lo = window[i].low
        left_h = max(window[j].high for j in range(i - l, i))
        right_h = max(window[j].high for j in range(i + 1, i + l + 1))
        left_l = min(window[j].low for j in range(i - l, i))
        right_l = min(window[j].low for j in range(i + 1, i + l + 1))
        if h > left_h and h > right_h and abs(h - mid) >= min_dist:
            highs.append((i, h))
        if lo < left_l and lo < right_l and abs(lo - mid) >= min_dist:
            lows.append((i, lo))
    highs.sort(key=lambda x: x[0])
    lows.sort(key=lambda x: x[0])
    sh = [p for _, p in highs[-cfg.swing_keep :]]
    sl = [p for _, p in lows[-cfg.swing_keep :]]
    return sh, sl
