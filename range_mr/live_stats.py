from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Signal

LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "live_settlements.jsonl"


def append_settlement(sig: Signal, stake: float, tz_name: str = "Asia/Shanghai") -> dict:
    row = {
        "signal_time": sig.signal_time,
        "settle_time": sig.settle_time,
        "settle_day": _day(sig.settle_time, tz_name),
        "symbol": sig.symbol,
        "side": sig.side,
        "logic": sig.logic,
        "regime": sig.regime,
        "result": sig.result,
        "payout_pnl": float(sig.payout_pnl or 0.0),
        "stake": float(stake),
        "open_px": sig.open_px,
        "settle_px": sig.settle_px,
    }
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def load_rows() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    rows: list[dict] = []
    with LEDGER_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def summarize_live(rows: list[dict], today: str) -> dict:
    done = [r for r in rows if r.get("result") in ("胜", "负", "平")]
    today_rows = [r for r in done if r.get("settle_day") == today]
    return {
        "all": _pack(done),
        "today": _pack(today_rows),
        "all_logic": _group(done, "logic"),
        "today_logic": _group(today_rows, "logic"),
        "all_symbol": _group(done, "symbol"),
        "today_symbol": _group(today_rows, "symbol"),
    }


def format_live_stats_tg(summary: dict, stake: float) -> str:
    lines = [
        f"<b>今日</b>  本金{stake:g}U",
        _line("合计", summary["today"]),
    ]
    for name, pack in summary["today_logic"].items():
        lines.append(_line(name, pack))
    lines.append("")
    lines.append("<b>累计</b>")
    lines.append(_line("合计", summary["all"]))
    for name, pack in summary["all_logic"].items():
        lines.append(_line(name, pack))
    if summary["all_symbol"]:
        lines.append("")
        for name, pack in summary["all_symbol"].items():
            lines.append(_line(name, pack))
    return "\n".join(lines)


def format_boot_stats(tz_name: str = "Asia/Shanghai", stake: float | None = None) -> str | None:
    rows = load_rows()
    if not rows:
        return None
    today = datetime.now(tz=ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    summary = summarize_live(rows, today)
    allp = summary["all"]
    if allp["n"] <= 0:
        return None
    extra = f" 本金{stake:g}U" if stake is not None else ""
    return (
        f"累计结算 {allp['n']}笔 {allp['win']}胜{allp['loss']}负 "
        f"胜率{allp['rate']:.1%} 盈亏{allp['pnl']:+.2f}{extra}"
    )


def _day(ts_ms: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d")


def _pack(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win": 0, "loss": 0, "flat": 0, "rate": 0.0, "pnl": 0.0}
    win = sum(1 for r in rows if r.get("result") == "胜")
    loss = sum(1 for r in rows if r.get("result") == "负")
    flat = sum(1 for r in rows if r.get("result") == "平")
    pnl = sum(float(r.get("payout_pnl") or 0.0) for r in rows)
    return {"n": n, "win": win, "loss": loss, "flat": flat, "rate": win / n, "pnl": pnl}


def _group(rows: list[dict], key: str) -> dict[str, dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[str(r.get(key) or "-")].append(r)
    return {k: _pack(v) for k, v in sorted(g.items())}


def _line(title: str, pack: dict) -> str:
    if pack["n"] <= 0:
        return f"{title}: 0笔"
    return (
        f"{title}: {pack['n']}笔 {pack['win']}胜{pack['loss']}负 "
        f"胜率{pack['rate']:.1%} 盈亏{pack['pnl']:+.2f}"
    )
