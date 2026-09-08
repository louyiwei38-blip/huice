from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from range_mr.binance_data import BinanceUMFutures
from range_mr.config import Config, INTERVAL_30M_MS, SETTLE_MS
from range_mr.replay import default_range, parse_day, replay
from range_mr.stats import print_conflict, print_signal, render_summary, summarize, write_csv


def _stdio_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")


def main() -> None:
    _stdio_utf8()
    parser = argparse.ArgumentParser(description="Range Mean Reversion V1 信号检测（不下单）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_replay = sub.add_parser("replay", help="回放历史 1m/30m 并结算")
    p_replay.add_argument("--days", type=int, default=7, help="回放最近 N 天（默认 7）")
    p_replay.add_argument("--start", type=str, default="", help="开始日期 YYYY-MM-DD（本地时区）")
    p_replay.add_argument("--end", type=str, default="", help="结束日期 YYYY-MM-DD（本地时区，不含当天则可只填 start）")
    p_replay.add_argument("--out", type=str, default="output/signals.csv")
    p_replay.add_argument("--print-signals", action="store_true", help="逐条打印信号")

    sub.add_parser("live", help="实时检测 BTC+ETH，信号推送到 Telegram")

    args = parser.parse_args()
    cfg = Config()

    if args.cmd == "replay":
        run_replay(cfg, args)
    elif args.cmd == "live":
        from range_mr.live import run_live

        run_live(cfg)


def run_replay(cfg: Config, args) -> None:
    tz = cfg.display_tz
    if args.start:
        start_ms = parse_day(args.start, tz)
        if args.end:
            end_ms = parse_day(args.end, tz)
        else:
            end_ms = int(datetime.now(tz=ZoneInfo(tz)).timestamp() * 1000)
    else:
        start_ms, end_ms = default_range(args.days, tz)

    warmup_ms = cfg.warmup_30m * INTERVAL_30M_MS
    fetch_30_start = start_ms - warmup_ms
    fetch_1m_end = end_ms + SETTLE_MS + 60_000

    print(
        f"拉取 {cfg.symbol} 30m/1m | "
        f"{_fmt(fetch_30_start, tz)} -> {_fmt(fetch_1m_end, tz)}"
    )
    client = BinanceUMFutures(cfg.binance_base)
    bars_30m = client.fetch_klines(cfg.symbol, "30m", fetch_30_start, fetch_1m_end)
    bars_1m = client.fetch_klines(cfg.symbol, "1m", start_ms - 60_000, fetch_1m_end)
    print(f"已拉取 30m={len(bars_30m)}  1m={len(bars_1m)}")
    if len(bars_30m) < cfg.warmup_30m or len(bars_1m) < 40:
        raise SystemExit("K 线数量不足，无法回放")

    signals, conflicts, _det = replay(bars_30m, bars_1m, cfg, start_ms, end_ms)
    if args.print_signals:
        for c in conflicts:
            print_conflict(c, cfg)
        for s in signals:
            print_signal(s, cfg)

        stats = summarize(signals, cfg.payout_rate, cfg.stake)
    text = render_summary(stats, len(conflicts))
    print()
    print(text)
    write_csv(args.out, signals, cfg)
    stats_path = os.path.splitext(args.out)[0] + "_stats.txt"
    with open(stats_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n明细: {args.out}")
    print(f"统计: {stats_path}")


def _fmt(ts_ms: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
