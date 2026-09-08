from __future__ import annotations

import csv
from pathlib import Path

from .models import Bar

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


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
