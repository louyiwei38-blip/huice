"""
从币安公开数据源下载 U 本位永续 1 分钟 K 线，按月落地，支持断点续传。

优先 data.binance.vision 月度 zip；缺失月份回退 REST。
"""
from __future__ import annotations

import calendar
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent / "data"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INTERVAL = "1m"
START = datetime(2023, 1, 1, tzinfo=timezone.utc)
# 略超过样本截止，保证 2026-09-01 前最后一笔 30 分钟结算价存在
END = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)

VISION_MONTHLY = (
    "https://data.binance.vision/data/futures/um/monthly/klines/"
    "{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"
)
VISION_DAILY = (
    "https://data.binance.vision/data/futures/um/daily/klines/"
    "{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}-{day:02d}.zip"
)
REST_URL = "https://fapi.binance.com/fapi/v1/klines"

COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
KEEP = ["open_time", "open", "high", "low", "close", "volume"]

HEADERS = {"User-Agent": "rfa30-replay/1.0"}


def _month_range(start: datetime, end: datetime) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _month_path(symbol: str, year: int, month: int) -> Path:
    return DATA_DIR / symbol / INTERVAL / f"{year}-{month:02d}.parquet"


def _expected_rows(year: int, month: int) -> int:
    days = calendar.monthrange(year, month)[1]
    return days * 1440


def _is_complete(path: Path, year: int, month: int, now: datetime) -> bool:
    """当前未结束的月份不算完整，必须重拉。"""
    month_end = datetime(year, month, calendar.monthrange(year, month)[1], 23, 59, tzinfo=timezone.utc)
    if now <= month_end:
        return False
    if not path.exists():
        return False
    try:
        df = pd.read_parquet(path, columns=["open_time"])
    except Exception:
        return False
    return len(df) >= int(_expected_rows(year, month) * 0.99)


def _to_open_time(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    sample = s.dropna()
    if sample.empty:
        return pd.to_datetime(s, utc=True)
    x = float(sample.iloc[0])
    if x > 1e16:
        unit = "ns"
    elif x > 1e14:
        unit = "us"
    else:
        unit = "ms"
    return pd.to_datetime(s, unit=unit, utc=True)


def _klines_from_csv_bytes(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if str(df.iloc[0, 0]).strip().lower() == "open_time":
        df = df.iloc[1:].reset_index(drop=True)
    n = min(len(COLUMNS), df.shape[1])
    df = df.iloc[:, :n].copy()
    df.columns = COLUMNS[:n]
    return _normalize(df)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df[KEEP].copy()
    out["open_time"] = _to_open_time(out["open_time"])
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open_time", "open", "high", "low", "close"])
    out = out.drop_duplicates(subset=["open_time"]).sort_values("open_time")
    return out.reset_index(drop=True)


def _get(url: str, timeout: int = 60) -> requests.Response:
    last_err = None
    for attempt in range(5):
        try:
            resp = requests.get(url, timeout=timeout, headers=HEADERS)
            if resp.status_code == 404:
                return resp
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下载失败 {url}: {last_err}")


def _download_monthly_zip(symbol: str, year: int, month: int) -> pd.DataFrame | None:
    url = VISION_MONTHLY.format(symbol=symbol, interval=INTERVAL, year=year, month=month)
    resp = _get(url)
    if resp.status_code == 404:
        return None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        return _klines_from_csv_bytes(zf.read(name))


def _download_daily_zip(symbol: str, year: int, month: int, day: int) -> pd.DataFrame | None:
    url = VISION_DAILY.format(
        symbol=symbol, interval=INTERVAL, year=year, month=month, day=day
    )
    resp = _get(url)
    if resp.status_code == 404:
        return None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        return _klines_from_csv_bytes(zf.read(name))


def _download_rest_chunk(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }
        last_err = None
        for attempt in range(5):
            try:
                resp = requests.get(REST_URL, params=params, timeout=30, headers=HEADERS)
                resp.raise_for_status()
                batch = resp.json()
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
        if last_err is not None:
            raise RuntimeError(f"REST 失败 {symbol} {cursor}: {last_err}")
        if not batch:
            break
        rows.extend(batch)
        last_open = int(batch[-1][0])
        nxt = last_open + 60_000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1500:
            break
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame(columns=KEEP)
    df = pd.DataFrame(rows, columns=COLUMNS)
    return _normalize(df)


def _download_month_daily_or_rest(symbol: str, year: int, month: int) -> pd.DataFrame:
    days = calendar.monthrange(year, month)[1]
    frames = []
    missing_days = []
    for day in range(1, days + 1):
        dt = datetime(year, month, day, tzinfo=timezone.utc)
        if dt > END:
            break
        if dt + pd.Timedelta(days=1) <= START:
            continue
        part = _download_daily_zip(symbol, year, month, day)
        if part is None or part.empty:
            missing_days.append(day)
        else:
            frames.append(part)
    if missing_days:
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        month_end = datetime(year, month, days, 23, 59, 59, tzinfo=timezone.utc)
        lo = max(month_start, START)
        hi = min(month_end, END)
        rest = _download_rest_chunk(symbol, int(lo.timestamp() * 1000), int(hi.timestamp() * 1000))
        if not rest.empty:
            frames.append(rest)
    if not frames:
        return pd.DataFrame(columns=KEEP)
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df


def download_month(symbol: str, year: int, month: int, force: bool = False) -> Path:
    path = _month_path(symbol, year, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    if not force and _is_complete(path, year, month, now):
        print(f"[skip] {symbol} {year}-{month:02d} 已存在", flush=True)
        return path

    print(f"[dl] {symbol} {year}-{month:02d}", flush=True)
    df = _download_monthly_zip(symbol, year, month)
    if df is None or df.empty:
        df = _download_month_daily_or_rest(symbol, year, month)
    if df is None or df.empty:
        raise RuntimeError(f"无数据 {symbol} {year}-{month:02d}")

    lo = max(datetime(year, month, 1, tzinfo=timezone.utc), START)
    hi_month = datetime(
        year, month, calendar.monthrange(year, month)[1], 23, 59, 59, tzinfo=timezone.utc
    )
    hi = min(hi_month, END)
    df = df[(df["open_time"] >= lo) & (df["open_time"] <= hi)]
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    df.to_parquet(path, index=False)
    print(f"[ok] {symbol} {year}-{month:02d} rows={len(df)} -> {path}", flush=True)
    return path


def last_closed_1m_open(now: datetime | None = None) -> datetime:
    """当前未走完的 1m 不算；返回最后一根已收盘 K 的 open_time。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return now.replace(second=0, microsecond=0) - timedelta(minutes=1)


def fetch_recent_1m(
    symbol: str,
    lookback_minutes: int = 7 * 1440,
    now: datetime | None = None,
) -> pd.DataFrame:
    """
    从币安 REST 拉最近已收盘 1m（不含正在形成的 K）。
    实盘检测用，不受回放 END 截断。
    """
    last_open = last_closed_1m_open(now)
    start = last_open - timedelta(minutes=max(int(lookback_minutes), 1) - 1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(last_open.timestamp() * 1000) + 59_999
    df = _download_rest_chunk(symbol, start_ms, end_ms)
    if df.empty:
        return df
    cutoff = pd.Timestamp(last_open)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    return df.loc[df["open_time"] <= cutoff].reset_index(drop=True)


def load_symbol_1m(symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
    """读取已下载的月度 parquet，拼成连续 1m 表。"""
    start = start or START
    end = end or END
    files = sorted((_month_path(symbol, y, m) for y, m in _month_range(start, end)))
    missing = [p for p in files if not p.exists()]
    if missing:
        raise FileNotFoundError("缺少月度数据，请先运行 python download.py: " + ", ".join(str(p) for p in missing[:6]))
    frames = [pd.read_parquet(p) for p in files if p.exists()]
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    df = df[(df["open_time"] >= start) & (df["open_time"] <= end)]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [(sym, y, m) for sym in SYMBOLS for y, m in _month_range(START, END)]
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(download_month, sym, y, m): (sym, y, m) for sym, y, m in jobs}
        for fut in as_completed(futs):
            job = futs[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append((job, exc))
                print(f"[err] {job}: {exc}")
    if errors:
        raise SystemExit(f"下载失败 {len(errors)} 个月份")
    print("下载完成。")


if __name__ == "__main__":
    main()
