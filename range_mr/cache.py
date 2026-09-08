from __future__ import annotations

import csv
from pathlib import Path

from .binance_data import BinanceUMFutures
from .config import INTERVAL_1M_MS, INTERVAL_30M_MS
from .models import Bar

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FIELDS = ("open_time", "close_time", "open", "high", "low", "close", "volume")
_INTERVAL_MS = {"1m": INTERVAL_1M_MS, "30m": INTERVAL_30M_MS}


def load_bars(path: str | Path) -> list[Bar]:
    out: list[Bar] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(
                Bar(
                    open_time=int(row["open_time"]),
                    close_time=int(row["close_time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return out


def load_cached(
    start_ms: int | None = None,
    end_ms: int | None = None,
    symbol: str = "btcusdt",
) -> tuple[list[Bar], list[Bar], int, int]:
    key = symbol.lower().replace("-", "")
    p30 = DATA_DIR / f"{key}_30m.csv"
    p1 = DATA_DIR / f"{key}_1m.csv"
    if not p30.exists() or not p1.exists():
        raise FileNotFoundError(f"缺少 {p30.name} 或 {p1.name}，先缓存K线")
    bars_30 = load_bars(p30)
    bars_1 = load_bars(p1)
    if start_ms is None:
        start_ms = bars_1[1].open_time
    if end_ms is None:
        end_ms = bars_1[-1].open_time
    return bars_30, bars_1, start_ms, end_ms


def save_bars(path: str | Path, bars: list[Bar]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for b in bars:
            w.writerow(
                {
                    "open_time": b.open_time,
                    "close_time": b.close_time,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
            )


def _merge(existing: list[Bar], extra: list[Bar]) -> list[Bar]:
    dedup: dict[int, Bar] = {b.open_time: b for b in existing}
    for b in extra:
        dedup[b.open_time] = b
    return [dedup[k] for k in sorted(dedup)]


def cache_path(symbol: str, interval: str) -> Path:
    key = symbol.lower().replace("-", "")
    return DATA_DIR / f"{key}_{interval}.csv"


def load_or_fetch_klines(
    client: BinanceUMFutures,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[Bar]:
    path = cache_path(symbol, interval)
    interval_ms = _INTERVAL_MS[interval]
    existing = load_bars(path) if path.exists() else []
    gaps: list[tuple[int, int]] = []
    if not existing:
        gaps.append((start_ms, end_ms))
    else:
        if existing[0].open_time > start_ms:
            gaps.append((start_ms, existing[0].open_time))
        have_end = existing[-1].open_time + interval_ms
        if have_end < end_ms:
            gaps.append((existing[-1].open_time + interval_ms, end_ms))
        else:
            print(f"缓存命中 {path.name}  {len(existing)} 根", flush=True)

    chunk_ms = 30 * 24 * 60 * 60 * 1000
    for gap_start, gap_end in gaps:
        cursor = gap_start
        while cursor < gap_end:
            nxt = min(cursor + chunk_ms, gap_end)
            print(f"拉取 {symbol} {interval} | {cursor} -> {nxt}", flush=True)
            extra = client.fetch_klines(symbol, interval, cursor, nxt)
            if extra:
                existing = _merge(existing, extra)
                save_bars(path, existing)
                print(f"已写入 {path.name}  共 {len(existing)} 根", flush=True)
                nxt_cursor = extra[-1].open_time + interval_ms
                if nxt_cursor <= cursor:
                    break
                cursor = nxt_cursor
            else:
                break

    return [b for b in existing if b.open_time >= start_ms and b.open_time < end_ms]
