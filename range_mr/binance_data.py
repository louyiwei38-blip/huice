from __future__ import annotations

import time
from typing import Optional

import requests

from .config import INTERVAL_1M_MS, INTERVAL_30M_MS
from .models import Bar

_INTERVAL_MS = {"1m": INTERVAL_1M_MS, "30m": INTERVAL_30M_MS}


class BinanceUMFutures:
    def __init__(self, base_url: str = "https://fapi.binance.com", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "range-mr-v1/1.0"})

    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        closed_only: bool = True,
    ) -> list[Bar]:
        interval_ms = _INTERVAL_MS[interval]
        out: list[Bar] = []
        cursor = start_ms
        now_ms = int(time.time() * 1000)

        while cursor < end_ms:
            raw = self._get_klines(symbol, interval, cursor, end_ms, limit=1500)
            if not raw:
                break
            for row in raw:
                bar = _parse_bar(row)
                if closed_only and bar.open_time + interval_ms > now_ms:
                    continue
                if bar.open_time >= end_ms:
                    continue
                out.append(bar)
            last_open = raw[-1][0]
            cursor = int(last_open) + interval_ms
            if len(raw) < 1500:
                break
            time.sleep(0.05)

        dedup: dict[int, Bar] = {}
        for bar in out:
            dedup[bar.open_time] = bar
        return [dedup[k] for k in sorted(dedup)]

    def fetch_closed_klines(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]:
        raw = self._get_klines(symbol, interval, start_ms=None, end_ms=None, limit=limit)
        now_ms = int(time.time() * 1000)
        interval_ms = _INTERVAL_MS[interval]
        bars = [_parse_bar(row) for row in raw]
        return [b for b in bars if b.open_time + interval_ms <= now_ms]

    def fetch_mark(self, symbol: str) -> tuple[int, float]:
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        resp = self.session.get(url, params={"symbol": symbol}, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        ts = int(data["time"])
        mark = float(data["markPrice"])
        last = float(data.get("indexPrice") or mark)
        px = mark if mark > 0 else last
        return ts, px

    def _get_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: Optional[int],
        end_ms: Optional[int],
        limit: int,
    ) -> list:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        url = f"{self.base_url}/fapi/v1/klines"
        last_err: Optional[Exception] = None
        for attempt in range(4):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_err = exc
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(f"Binance klines failed: {last_err}") from last_err


def _parse_bar(row: list) -> Bar:
    return Bar(
        open_time=int(row[0]),
        close_time=int(row[6]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )
