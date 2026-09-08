from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config, INTERVAL_1M_MS, INTERVAL_30M_MS, SETTLE_MS
from .models import Bar, Conflict, Signal
from .detector import Detector


TZ_UTC = timezone.utc


def replay(
    bars_30m: list[Bar],
    bars_1m: list[Bar],
    cfg: Config,
    start_ms: int,
    end_ms: int,
) -> tuple[list[Signal], list[Conflict], Detector]:
    det = Detector(cfg)
    warmup = [b for b in bars_30m if b.open_time + INTERVAL_30M_MS <= start_ms]
    for bar in warmup:
        det.on_30m_close(bar)

    pending_30m = [b for b in bars_30m if b.open_time + INTERVAL_30M_MS > start_ms]
    p = 0
    signals: list[Signal] = []
    conflicts: list[Conflict] = []
    prev_close: float | None = None

    for m in bars_1m:
        if m.open_time < start_ms:
            prev_close = m.close
            continue
        if m.open_time >= end_ms:
            break
        while p < len(pending_30m) and pending_30m[p].open_time + INTERVAL_30M_MS <= m.open_time:
            det.on_30m_close(pending_30m[p])
            p += 1
        if prev_close is None:
            prev_close = m.close
            continue
        result = det.on_1m(m, prev_close)
        signals.extend(result.signals)
        conflicts.extend(result.conflicts)
        prev_close = m.close

    index = {b.open_time: b for b in bars_1m}
    for sig in signals:
        settle(sig, index, cfg.payout_rate, cfg.stake)
    return signals, conflicts, det


def apply_binary_payout(sig: Signal, payout_rate: float, stake: float) -> None:
    if sig.result == "胜":
        sig.payout_pnl = stake * payout_rate
    elif sig.result == "负":
        sig.payout_pnl = -stake
    elif sig.result == "平":
        sig.payout_pnl = 0.0
    else:
        sig.payout_pnl = None


def settle(
    sig: Signal,
    index_1m: dict[int, Bar],
    payout_rate: float = 0.85,
    stake: float = 1.0,
) -> None:
    sig.settle_time = sig.signal_time + SETTLE_MS
    minute = sig.settle_time - (sig.settle_time % INTERVAL_1M_MS)
    bar = index_1m.get(minute)
    if bar is None:
        sig.result = "缺失"
        return
    sig.settle_px = bar.close
    if sig.side == "LONG":
        sig.pnl_abs = sig.settle_px - sig.open_px
    else:
        sig.pnl_abs = sig.open_px - sig.settle_px
    sig.pnl_pct = sig.pnl_abs / sig.open_px if sig.open_px else 0.0
    if sig.pnl_abs > 0:
        sig.result = "胜"
    elif sig.pnl_abs < 0:
        sig.result = "负"
    else:
        sig.result = "平"
    apply_binary_payout(sig, payout_rate, stake)


def parse_day(text: str, tz_name: str) -> int:
    tz = ZoneInfo(tz_name)
    dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=tz)
    return int(dt.astimezone(TZ_UTC).timestamp() * 1000)


def default_range(days: int, tz_name: str) -> tuple[int, int]:
    tz = ZoneInfo(tz_name)
    end = datetime.now(tz=tz).replace(second=0, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.astimezone(TZ_UTC).timestamp() * 1000), int(end.astimezone(TZ_UTC).timestamp() * 1000)
