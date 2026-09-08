from __future__ import annotations

from datetime import datetime, timezone

from .config import Config, INTERVAL_30M_MS, SETTLE_MS
from .models import Bar, Conflict, DetectResult, FlipState, Signal, Structure
from .structure import compute_structure


def next_30m_boundary(ts_ms: int) -> int:
    return (ts_ms // INTERVAL_30M_MS + 1) * INTERVAL_30M_MS


def cooldown_until(ts_ms: int, bars: int) -> int:
    t = ts_ms
    for _ in range(max(1, bars)):
        t = next_30m_boundary(t)
    return t


class Detector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.closed_30m: list[Bar] = []
        self.structure: Structure | None = None
        self.prior_for_break: Structure | None = None
        self.flip: FlipState | None = None
        self.cooldowns: dict[tuple[str, str], int] = {}
        self.last_price: float | None = None
        self._bar_open: float = 0.0
        self._bar_close: float = 0.0
        self.filter_stats: dict[str, int] = {
            "scan": 0,
            "flip_only": 0,
            "ranging_block": 0,
            "candidates": 0,
            "with_bar": 0,
            "max_er": 0,
            "chase": 0,
            "cooldown": 0,
            "conflict": 0,
            "emitted": 0,
        }

    def pop_filter_stats(self) -> dict[str, int]:
        out = dict(self.filter_stats)
        for k in self.filter_stats:
            self.filter_stats[k] = 0
        return out

    def on_30m_close(self, bar: Bar) -> None:
        if self.closed_30m and bar.open_time <= self.closed_30m[-1].open_time:
            return
        prior = list(self.closed_30m)
        prior_st = compute_structure(prior, self.cfg) if prior else None
        self.prior_for_break = prior_st

        if prior_st is not None:
            self._update_flip_on_close(bar, prior_st)

        self.closed_30m.append(bar)
        if len(self.closed_30m) > 400:
            self.closed_30m = self.closed_30m[-300:]
        self.structure = compute_structure(self.closed_30m, self.cfg)

    def on_1m(self, bar: Bar, prev_close: float) -> DetectResult:
        px_for_conflict = bar.close
        self._bar_open = bar.open
        self._bar_close = bar.close
        candidates = self._scan_range(
            ts=bar.open_time,
            prev_px=prev_close,
            low=bar.low,
            high=bar.high,
            open_px=bar.open,
            last_px=bar.close,
        )
        candidates = self._quality_filter(bar, candidates)
        self.last_price = bar.close
        return self._emit(bar.open_time, px_for_conflict, candidates)

    def on_tick(self, ts_ms: int, price: float) -> DetectResult:
        prev = self.last_price if self.last_price is not None else price
        low = min(prev, price)
        high = max(prev, price)
        self._bar_open = prev
        self._bar_close = price
        candidates = self._scan_range(
            ts=ts_ms,
            prev_px=prev,
            low=low,
            high=high,
            open_px=price,
            last_px=price,
        )
        fake = Bar(
            open_time=ts_ms,
            close_time=ts_ms,
            open=prev,
            high=high,
            low=low,
            close=price,
            volume=0.0,
        )
        candidates = self._quality_filter(fake, candidates)
        self.last_price = price
        return self._emit(ts_ms, price, candidates)

    def _scan_range(
        self,
        ts: int,
        prev_px: float,
        low: float,
        high: float,
        open_px: float,
        last_px: float,
    ) -> list[Signal]:
        st = self.structure
        if st is None:
            return []
        self.filter_stats["scan"] += 1
        out: list[Signal] = []
        if self.flip is not None:
            if ts >= self.flip.expire_ts:
                self.flip = None
            else:
                travel = abs(last_px - self.flip.breakout_level)
                if travel >= self.cfg.flip_travel_mult * self.flip.old_height:
                    self.flip = None
                else:
                    self.filter_stats["flip_only"] += 1
                    if "SR_FLIP" in self.cfg.enabled_logics:
                        sig = self._maybe_flip(ts, prev_px, low, high, open_px, last_px, st)
                        if sig:
                            out.append(sig)
                    return out

        if self.cfg.require_ranging and not st.is_ranging:
            self.filter_stats["ranging_block"] += 1
            return out

        if "BOX_EDGE" in self.cfg.enabled_logics:
            box = self._maybe_box_edge(ts, prev_px, low, high, open_px, last_px, st)
            if box:
                out.append(box)
        if "HVN" in self.cfg.enabled_logics:
            hvn = self._maybe_hvn(ts, prev_px, low, high, open_px, last_px, st)
            if hvn:
                out.append(hvn)
        if "SWING" in self.cfg.enabled_logics:
            swing = self._maybe_swing(ts, prev_px, low, high, open_px, last_px, st)
            if swing:
                out.append(swing)
        return out

    def _quality_filter(self, bar: Bar, candidates: list[Signal]) -> list[Signal]:
        cfg = self.cfg
        st = self.structure
        utc = datetime.fromtimestamp(bar.open_time / 1000.0, tz=timezone.utc)
        if cfg.utc_weekdays is not None and utc.weekday() not in cfg.utc_weekdays:
            return []
        if cfg.utc_hours is not None and utc.hour not in cfg.utc_hours:
            return []
        out: list[Signal] = []
        for sig in candidates:
            self.filter_stats["candidates"] += 1
            if sig.logic not in cfg.enabled_logics:
                continue
            if sig.side not in cfg.allowed_sides:
                continue
            if cfg.require_with_bar:
                if sig.side == "LONG" and bar.close < bar.open:
                    self.filter_stats["with_bar"] += 1
                    continue
                if sig.side == "SHORT" and bar.close > bar.open:
                    self.filter_stats["with_bar"] += 1
                    continue
            if cfg.max_er is not None and sig.regime == "RANGE" and sig.er > cfg.max_er:
                self.filter_stats["max_er"] += 1
                continue
            if cfg.skip_chase_atr > 0 and st is not None:
                chase = cfg.skip_chase_atr * st.atr
                if sig.side == "LONG" and bar.close < st.range_low - chase:
                    self.filter_stats["chase"] += 1
                    continue
                if sig.side == "SHORT" and bar.close > st.range_high + chase:
                    self.filter_stats["chase"] += 1
                    continue
            out.append(sig)
        return out

    def _emit(self, ts: int, price: float, candidates: list[Signal]) -> DetectResult:
        if not candidates:
            return DetectResult()
        sides = {s.side for s in candidates}
        if "LONG" in sides and "SHORT" in sides:
            self.filter_stats["conflict"] += 1
            return DetectResult(
                conflicts=[
                    Conflict(
                        signal_time=ts,
                        sides=sorted(sides),
                        logics=[s.logic for s in candidates],
                        price=price,
                    )
                ]
            )
        if len(candidates) < self.cfg.min_confluence:
            return DetectResult()
        if self.cfg.merge_same_ts or self.cfg.min_confluence >= 2:
            primary = candidates[0]
            logics = "+".join(sorted({s.logic for s in candidates}))
            primary.logic = logics
            primary.reason = f"共振 {logics} | {primary.reason}"
            candidates = [primary]
        kept: list[Signal] = []
        for sig in candidates:
            key = (sig.logic, sig.side)
            until = self.cooldowns.get(key, 0)
            if ts < until:
                self.filter_stats["cooldown"] += 1
                continue
            self.cooldowns[key] = cooldown_until(ts, self.cfg.cooldown_bars)
            kept.append(sig)
        self.filter_stats["emitted"] += len(kept)
        return DetectResult(signals=kept)

    def _update_flip_on_close(self, bar: Bar, prior: Structure) -> None:
        atr = prior.atr
        buf = self.cfg.breakout_atr_mult * atr

        if self.flip is not None:
            if bar.open_time + INTERVAL_30M_MS >= self.flip.expire_ts:
                self.flip = None
            else:
                if self.flip.direction == "up":
                    if bar.close <= self.flip.old_range_high - buf:
                        self.flip = None
                    elif abs(bar.close - self.flip.breakout_level) >= self.cfg.flip_travel_mult * self.flip.old_height:
                        self.flip = None
                else:
                    if bar.close >= self.flip.old_range_low + buf:
                        self.flip = None
                    elif abs(bar.close - self.flip.breakout_level) >= self.cfg.flip_travel_mult * self.flip.old_height:
                        self.flip = None
            if self.flip is not None:
                return

        up = bar.close > prior.range_high and (bar.close - prior.range_high) >= buf
        down = bar.close < prior.range_low and (prior.range_low - bar.close) >= buf
        if not up and not down:
            return

        confirm_ts = bar.open_time + INTERVAL_30M_MS
        expire_ts = confirm_ts + self.cfg.flip_bars * INTERVAL_30M_MS
        if up:
            levels = [prior.range_high]
            if prior.vah >= prior.range_mid:
                levels.append(prior.vah)
            levels.extend(h for h in prior.swing_highs if h <= bar.close)
            self.flip = FlipState(
                direction="up",
                levels=_uniq_levels(levels),
                breakout_level=prior.range_high,
                old_range_high=prior.range_high,
                old_range_low=prior.range_low,
                old_height=prior.range_height,
                atr=atr,
                confirm_ts=confirm_ts,
                expire_ts=expire_ts,
            )
        else:
            levels = [prior.range_low]
            if prior.val <= prior.range_mid:
                levels.append(prior.val)
            levels.extend(h for h in prior.swing_lows if h >= bar.close)
            self.flip = FlipState(
                direction="down",
                levels=_uniq_levels(levels),
                breakout_level=prior.range_low,
                old_range_high=prior.range_high,
                old_range_low=prior.range_low,
                old_height=prior.range_height,
                atr=atr,
                confirm_ts=confirm_ts,
                expire_ts=expire_ts,
            )

    def _maybe_box_edge(
        self, ts: int, prev: float, low: float, high: float, open_px: float, last: float, st: Structure
    ) -> Signal | None:
        if (
            _overlap(low, high, st.upper_band_low, st.range_high)
            and low <= st.range_high
            and self._crossed(prev, st.upper_band_low, st.range_high)
        ):
            px = _first_touch(prev, open_px, low, high, st.upper_band_low, st.range_high)
            return self._sig(ts, "SHORT", "BOX_EDGE", px, st, st.range_high, "价格进入箱体上沿带")
        if (
            _overlap(low, high, st.range_low, st.lower_band_high)
            and high >= st.range_low
            and self._crossed(prev, st.range_low, st.lower_band_high)
        ):
            px = _first_touch(prev, open_px, low, high, st.range_low, st.lower_band_high)
            return self._sig(ts, "LONG", "BOX_EDGE", px, st, st.range_low, "价格进入箱体下沿带")
        return None

    def _maybe_hvn(
        self, ts: int, prev: float, low: float, high: float, open_px: float, last: float, st: Structure
    ) -> Signal | None:
        tol = st.touch_tol
        short_hit: tuple[float, float, float] | None = None
        long_hit: tuple[float, float, float] | None = None

        vah_lo, vah_hi = st.vah - tol, st.vah + tol
        val_lo, val_hi = st.val - tol, st.val + tol
        if (
            st.vah >= st.upper_band_low
            and _overlap(low, high, vah_lo, vah_hi)
            and self._crossed(prev, vah_lo, vah_hi)
        ):
            short_hit = (vah_lo, vah_hi, st.vah)
        if (
            st.val <= st.lower_band_high
            and _overlap(low, high, val_lo, val_hi)
            and self._crossed(prev, val_lo, val_hi)
        ):
            long_hit = (val_lo, val_hi, st.val)

        for z in st.hvn_zones:
            if z.side == "upper" and z.center < st.upper_band_low:
                continue
            if z.side == "lower" and z.center > st.lower_band_high:
                continue
            zlo, zhi = z.low - tol, z.high + tol
            if not _overlap(low, high, zlo, zhi) or not self._crossed(prev, zlo, zhi):
                continue
            if z.side == "upper":
                short_hit = (zlo, zhi, z.center)
            else:
                long_hit = (zlo, zhi, z.center)

        if short_hit and long_hit:
            return None
        if short_hit:
            lo, hi, lvl = short_hit
            px = _first_touch(prev, open_px, low, high, lo, hi)
            return self._sig(ts, "SHORT", "HVN", px, st, lvl, "价格进入偏上成交密集区/VAH")
        if long_hit:
            lo, hi, lvl = long_hit
            px = _first_touch(prev, open_px, low, high, lo, hi)
            return self._sig(ts, "LONG", "HVN", px, st, lvl, "价格进入偏下成交密集区/VAL")
        return None

    def _maybe_swing(
        self, ts: int, prev: float, low: float, high: float, open_px: float, last: float, st: Structure
    ) -> Signal | None:
        tol = st.touch_tol
        short_lvl = None
        long_lvl = None
        for sh in st.swing_highs:
            if high >= sh - tol and low <= sh + tol and self._crossed(prev, sh - tol, sh + tol):
                short_lvl = sh
                break
        for sl in st.swing_lows:
            if high >= sl - tol and low <= sl + tol and self._crossed(prev, sl - tol, sl + tol):
                long_lvl = sl
                break
        if short_lvl is not None and long_lvl is not None:
            return None
        if short_lvl is not None:
            px = _first_touch(prev, open_px, low, high, short_lvl - tol, short_lvl + tol)
            return self._sig(ts, "SHORT", "SWING", px, st, short_lvl, "价格触及阶段性前高")
        if long_lvl is not None:
            px = _first_touch(prev, open_px, low, high, long_lvl - tol, long_lvl + tol)
            return self._sig(ts, "LONG", "SWING", px, st, long_lvl, "价格触及阶段性前低")
        return None

    def _maybe_flip(
        self, ts: int, prev: float, low: float, high: float, open_px: float, last: float, st: Structure
    ) -> Signal | None:
        flip = self.flip
        if flip is None:
            return None
        tol = max(st.touch_tol, 0.15 * flip.atr)
        best: tuple[float, float] | None = None
        if flip.direction == "up":
            for lvl in flip.levels:
                if prev > lvl + tol and low <= lvl + tol and high >= lvl:
                    best = (lvl, _first_touch(prev, open_px, low, high, lvl - tol, lvl + tol))
                    break
            if best:
                lvl, px = best
                return self._sig(
                    ts, "LONG", "SR_FLIP", px, st, lvl,
                    f"向上突破后回踩旧阻力转支撑 {lvl:.1f}",
                    regime="BREAKOUT",
                )
        else:
            for lvl in flip.levels:
                if prev < lvl - tol and high >= lvl - tol and low <= lvl:
                    best = (lvl, _first_touch(prev, open_px, low, high, lvl - tol, lvl + tol))
                    break
            if best:
                lvl, px = best
                return self._sig(
                    ts, "SHORT", "SR_FLIP", px, st, lvl,
                    f"向下跌破后回抽旧支撑转阻力 {lvl:.1f}",
                    regime="BREAKOUT",
                )
        return None

    def _sig(
        self,
        ts: int,
        side: str,
        logic: str,
        px: float,
        st: Structure,
        level: float,
        reason: str,
        regime: str | None = None,
    ) -> Signal:
        return Signal(
            signal_time=ts,
            side=side,
            logic=logic,
            open_px=px,
            regime=regime or ("RANGE" if st.is_ranging else "TREND"),
            reason=reason,
            range_high=st.range_high,
            range_low=st.range_low,
            poc=st.poc,
            vah=st.vah,
            val=st.val,
            trigger_level=level,
            settle_time=ts + SETTLE_MS,
            atr=st.atr,
            bar_open=self._bar_open,
            bar_close=self._bar_close,
            er=st.er,
            symbol=self.cfg.symbol,
        )


    def _crossed(self, prev: float, zlo: float, zhi: float) -> bool:
        if not self.cfg.require_cross:
            return True
        return prev < zlo or prev > zhi


def _overlap(a1: float, a2: float, b1: float, b2: float) -> bool:
    return min(a2, b2) >= max(a1, b1)


def _first_touch(prev: float, open_px: float, low: float, high: float, zlo: float, zhi: float) -> float:
    if prev < zlo and high >= zlo:
        px = zlo
    elif prev > zhi and low <= zhi:
        px = zhi
    elif zlo <= open_px <= zhi:
        px = open_px
    else:
        px = min(max(open_px, zlo), zhi)
    return min(max(px, low), high)


def _uniq_levels(levels: list[float], rel: float = 0.0002) -> list[float]:
    out: list[float] = []
    for x in sorted(levels, reverse=True):
        if not out or abs(x - out[-1]) / max(abs(x), 1.0) > rel:
            out.append(x)
    return out
