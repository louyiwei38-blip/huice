from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import Config
from .models import Conflict, Signal


def format_ts(ts_ms: int, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def summarize(signals: list[Signal], payout_rate: float = 0.85, stake: float = 1.0) -> dict:
    done = [s for s in signals if s.result in ("胜", "负", "平")]
    wins = [s for s in done if s.result == "胜"]
    losses = [s for s in done if s.result == "负"]
    flats = [s for s in done if s.result == "平"]
    missing = [s for s in signals if s.result == "缺失"]
    avg = (sum(s.pnl_pct or 0.0 for s in done) / len(done)) if done else 0.0
    be = 1.0 / (1.0 + payout_rate) if payout_rate > 0 else 1.0

    def binary_pnl(rows: list[Signal]) -> float:
        total = 0.0
        for s in rows:
            if s.payout_pnl is not None:
                total += s.payout_pnl
            elif s.result == "胜":
                total += stake * payout_rate
            elif s.result == "负":
                total -= stake
        return total

    payout_pnl = binary_pnl(done)
    by_logic: dict[str, list[Signal]] = defaultdict(list)
    by_side: dict[str, list[Signal]] = defaultdict(list)
    by_regime: dict[str, list[Signal]] = defaultdict(list)
    by_symbol: dict[str, list[Signal]] = defaultdict(list)
    for s in done:
        by_logic[s.logic].append(s)
        by_side[s.side].append(s)
        by_regime[s.regime].append(s)
        by_symbol[s.symbol or "-"].append(s)

    def pack(rows: list[Signal]) -> dict:
        if not rows:
            return {"n": 0, "win": 0, "rate": 0.0, "avg_pct": 0.0, "payout_pnl": 0.0, "ev": 0.0}
        w = sum(1 for x in rows if x.result == "胜")
        pnl = binary_pnl(rows)
        return {
            "n": len(rows),
            "win": w,
            "rate": w / len(rows),
            "avg_pct": sum(x.pnl_pct or 0.0 for x in rows) / len(rows),
            "payout_pnl": pnl,
            "ev": pnl / len(rows),
        }

    return {
        "total": len(signals),
        "settled": len(done),
        "win": len(wins),
        "loss": len(losses),
        "flat": len(flats),
        "missing": len(missing),
        "win_rate": (len(wins) / len(done)) if done else 0.0,
        "avg_pct": avg,
        "payout_rate": payout_rate,
        "stake": stake,
        "breakeven": be,
        "payout_pnl": payout_pnl,
        "ev": (payout_pnl / len(done)) if done else 0.0,
        "by_logic": {k: pack(v) for k, v in sorted(by_logic.items())},
        "by_side": {k: pack(v) for k, v in sorted(by_side.items())},
        "by_regime": {k: pack(v) for k, v in sorted(by_regime.items())},
        "by_symbol": {k: pack(v) for k, v in sorted(by_symbol.items())},
    }


def render_summary(stats: dict, conflicts: int) -> str:
    be = stats.get("breakeven", 0.0)
    pr = stats.get("payout_rate", 0.85)
    lines = [
        "===== Range Mean Reversion V1.1 结算统计 =====",
        f"信号总数: {stats['total']}  已结算: {stats['settled']}  冲突跳过: {conflicts}",
        f"胜: {stats['win']}  负: {stats['loss']}  平: {stats['flat']}  缺失: {stats['missing']}",
        f"胜率: {stats['win_rate']:.2%}  （{pr:.0%}支付盈亏平衡胜率 {be:.2%}）",
        f"支付率: {pr:.0%}  每注本金: {stats.get('stake', 250):g}  盈亏合计: {stats.get('payout_pnl', 0):+.2f}  单笔EV: {stats.get('ev', 0):+.4f}",
        f"方向价差均收益: {stats['avg_pct']:.4%}",
        "",
        "按标的:",
    ]
    for k, v in stats.get("by_symbol", {}).items():
        lines.append(
            f"  {k:10s} n={v['n']:4d}  胜率={v['rate']:.2%}  盈亏={v['payout_pnl']:+.2f}  EV={v['ev']:+.4f}"
        )
    lines.append("按逻辑:")
    for k, v in stats["by_logic"].items():
        lines.append(
            f"  {k:10s} n={v['n']:4d}  胜率={v['rate']:.2%}  盈亏={v['payout_pnl']:+.2f}  EV={v['ev']:+.4f}"
        )
    lines.append("按方向:")
    for k, v in stats["by_side"].items():
        lines.append(
            f"  {k:10s} n={v['n']:4d}  胜率={v['rate']:.2%}  盈亏={v['payout_pnl']:+.2f}  EV={v['ev']:+.4f}"
        )
    lines.append("按状态:")
    for k, v in stats["by_regime"].items():
        lines.append(
            f"  {k:10s} n={v['n']:4d}  胜率={v['rate']:.2%}  盈亏={v['payout_pnl']:+.2f}  EV={v['ev']:+.4f}"
        )
    return "\n".join(lines)


def write_csv(path: str, signals: list[Signal], cfg: Config) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fields = [
        "signal_time",
        "signal_time_local",
        "symbol",
        "side",
        "logic",
        "open_px",
        "settle_time",
        "settle_time_local",
        "settle_px",
        "pnl_abs",
        "pnl_pct",
        "payout_pnl",
        "result",
        "regime",
        "trigger_level",
        "range_high",
        "range_low",
        "poc",
        "vah",
        "val",
        "reason",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in signals:
            w.writerow(
                {
                    "signal_time": s.signal_time,
                    "signal_time_local": format_ts(s.signal_time, cfg.display_tz),
                    "symbol": s.symbol,
                    "side": s.side,
                    "logic": s.logic,
                    "open_px": f"{s.open_px:.2f}",
                    "settle_time": s.settle_time,
                    "settle_time_local": format_ts(s.settle_time, cfg.display_tz),
                    "settle_px": "" if s.settle_px is None else f"{s.settle_px:.2f}",
                    "pnl_abs": "" if s.pnl_abs is None else f"{s.pnl_abs:.2f}",
                    "pnl_pct": "" if s.pnl_pct is None else f"{s.pnl_pct:.6f}",
                    "payout_pnl": "" if s.payout_pnl is None else f"{s.payout_pnl:.4f}",
                    "result": s.result or "",
                    "regime": s.regime,
                    "trigger_level": f"{s.trigger_level:.2f}",
                    "range_high": f"{s.range_high:.2f}",
                    "range_low": f"{s.range_low:.2f}",
                    "poc": f"{s.poc:.2f}",
                    "vah": f"{s.vah:.2f}",
                    "val": f"{s.val:.2f}",
                    "reason": s.reason,
                }
            )


def print_signal(sig: Signal, cfg: Config) -> None:
    t = format_ts(sig.signal_time, cfg.display_tz)
    print(
        f"[{t}] {sig.symbol or cfg.symbol} {sig.side:5s} {sig.logic:8s} "
        f"开仓价={sig.open_px:.1f} 触发位={sig.trigger_level:.1f} {sig.regime} | {sig.reason}"
    )


def print_conflict(c: Conflict, cfg: Config) -> None:
    t = format_ts(c.signal_time, cfg.display_tz)
    print(f"[{t}] CONFLICT {c.logics} @ {c.price:.1f} | {c.reason}")
