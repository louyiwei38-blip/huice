"""
RSI_BB 实盘：扫最近已收盘 5m，开仓/结算推 Telegram，并记盈亏账本。
阈值与回放相同：RSI(7) 20/80、布林 k=2.2；不跳过资金费窗口。禁止改参。

  python live.py --test          # 测 Telegram
  python live.py --test-copybot  # 检查 webhook 配置（不下单）
  python live.py --once          # 扫一轮（适合 cron）
  python live.py --loop          # 对齐 5m 收盘循环：推送 + 自动下单
  python live.py --stats         # 只推/打印账本
  python live.py --once --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from copybot import copybot_amount, copybot_enabled, copybot_token, get_client, place_for_signal
from download import SYMBOLS, fetch_recent_1m
from rsi_bb import scan_rsi_bb
from strategy import (
    HOLD_MINUTES,
    RSI_BB_K,
    RSI_BB_OB,
    RSI_BB_OS,
    RSI_BB_PERIOD,
    SKIP_SESSION_BJ_LABEL,
)
from tg import html_escape, send_telegram

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"
LEDGER_PATH = OUT_DIR / "live_ledger.json"
PNL_CSV = OUT_DIR / "live_pnl.csv"

LOOKBACK_MINUTES = 7 * 1440
WARM_LOOKBACK_MINUTES = 36 * 60
ENTRY_NOTIFY_SEC = 12 * 60
SETTLE_NOTIFY_SEC = 12 * 60
FIVE_MIN = 5 * 60
BAR_LAG_SEC = 20  # 5m 收盘后再等，避免币安 K 线未落库


def log(msg: str, *, err: bool = False) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"{ts} {msg}", flush=True, file=sys.stderr if err else sys.stdout)


def _utc(ts: Any) -> datetime:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.to_pydatetime()


def _iso(ts: Any) -> str | None:
    if ts is None or (isinstance(ts, float) and math.isnan(ts)):
        return None
    try:
        if pd.isna(ts):
            return None
    except (TypeError, ValueError):
        pass
    return _utc(ts).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return _utc(s)


def trade_id(symbol: str, timestamp: Any) -> str:
    return f"{symbol}|{_iso(timestamp)}"


def empty_ledger() -> dict[str, Any]:
    return {"version": 1, "last_daily_utc": None, "trades": {}}


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    path = path or LEDGER_PATH
    if not path.exists():
        return empty_ledger()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("last_daily_utc", None)
    data.setdefault("trades", {})
    for rec in data["trades"].values():
        if "seed" not in rec:
            rec["seed"] = True
    return data


def save_ledger(ledger: dict[str, Any], path: Path | None = None) -> None:
    path = path or LEDGER_PATH
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    rows = []
    for rec in sorted(ledger["trades"].values(), key=lambda r: r.get("timestamp") or ""):
        if rec.get("void") or rec.get("pnl") is None or rec.get("seed"):
            continue
        rows.append(
            {
                "timestamp": rec.get("timestamp"),
                "symbol": rec.get("symbol"),
                "bias": rec.get("bias"),
                "entry": rec.get("entry"),
                "settle_time": rec.get("settle_time"),
                "settle": rec.get("settle"),
                "pnl": rec.get("pnl"),
                "win": rec.get("pnl") is not None and float(rec["pnl"]) > 0,
            }
        )
    pd.DataFrame(rows).to_csv(PNL_CSV, index=False)


def _num(row: pd.Series, key: str) -> float | None:
    if key not in row.index:
        return None
    v = row[key]
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return float(v)


def row_to_rec(row: pd.Series) -> dict[str, Any]:
    pnl = _num(row, "pnl")
    void = bool(row.get("void", False))
    return {
        "symbol": str(row["symbol"]),
        "timestamp": _iso(row["timestamp"]),
        "bias": int(row["bias"]),
        "entry": _num(row, "entry"),
        "rsi": _num(row, "rsi"),
        "bb_lower_k": _num(row, "bb_lower_k"),
        "bb_upper_k": _num(row, "bb_upper_k"),
        "settle_time": _iso(row.get("settle_time")),
        "settle": _num(row, "settle"),
        "pnl": pnl,
        "void": void,
        "notified_entry": False,
        "notified_settle": False,
        "copybot": None,
    }


def _age_sec(ts_iso: str | None, now: datetime) -> float | None:
    t = _parse_iso(ts_iso)
    if t is None:
        return None
    return (now - t).total_seconds()


def ingest_trades(
    ledger: dict[str, Any],
    trades: pd.DataFrame,
    now: datetime,
    *,
    entry_max_age: float = ENTRY_NOTIFY_SEC,
    settle_max_age: float = SETTLE_NOTIFY_SEC,
) -> list[tuple[str, dict[str, Any]]]:
    """
    合并扫描结果。过旧的入场/结算只记账不推送，避免首次运行刷屏。
    返回待发送事件：("entry"|"settle", rec)。
    """
    events: list[tuple[str, dict[str, Any]]] = []
    if trades is None or trades.empty:
        return events
    book = ledger["trades"]
    for _, row in trades.iterrows():
        rec = row_to_rec(row)
        tid = trade_id(rec["symbol"], rec["timestamp"])
        prev = book.get(tid)
        entry_age = _age_sec(rec["timestamp"], now)
        settle_age = _age_sec(rec["settle_time"], now)
        settled = rec["pnl"] is not None or rec["void"]

        if prev is None:
            send_entry = (
                not rec["void"]
                and entry_age is not None
                and 0 <= entry_age <= entry_max_age
            )
            send_settle = (
                settled
                and settle_age is not None
                and 0 <= settle_age <= settle_max_age
            )
            rec["notified_entry"] = not send_entry
            rec["notified_settle"] = (not send_settle) if settled else False
            rec["seed"] = not (send_entry or send_settle)
            book[tid] = rec
            if send_entry:
                events.append(("entry", rec))
            if send_settle:
                events.append(("settle", rec))
            continue

        was_open = prev.get("pnl") is None
        kept_copybot = prev.get("copybot")
        for k in (
            "entry",
            "rsi",
            "bb_lower_k",
            "bb_upper_k",
            "settle_time",
            "settle",
            "pnl",
            "void",
            "bias",
        ):
            if rec.get(k) is not None or k in ("pnl", "void", "settle", "settle_time"):
                prev[k] = rec[k]
        if kept_copybot:
            prev["copybot"] = kept_copybot
        now_settled = prev.get("pnl") is not None or prev.get("void")
        if was_open and now_settled and not prev.get("notified_settle"):
            if settle_age is not None and 0 <= settle_age <= settle_max_age:
                events.append(("settle", prev))
                prev["seed"] = False
            else:
                prev["notified_settle"] = True
        if (
            not prev.get("notified_entry")
            and not prev.get("void")
            and entry_age is not None
            and 0 <= entry_age <= entry_max_age
        ):
            events.append(("entry", prev))
    return events


def _closed(rec: dict[str, Any]) -> bool:
    return (not rec.get("void")) and rec.get("pnl") is not None and not rec.get("seed")


def _in_range(ts_iso: str | None, start: datetime, end: datetime) -> bool:
    t = _parse_iso(ts_iso)
    if t is None:
        return False
    return start <= t < end


def trade_amount(rec: dict[str, Any] | None = None) -> float:
    """该笔名义下单金额（U）。优先用跟单回执里的 orderAmount，否则用环境默认。"""
    if isinstance(rec, dict):
        cb = rec.get("copybot")
        if isinstance(cb, dict):
            raw = (cb.get("payload") or {}).get("orderAmount")
            if raw is not None and str(raw).strip() != "":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
    return float(copybot_amount())


def unit_to_usdt(unit_pnl: float | None, amount: float) -> float | None:
    if unit_pnl is None:
        return None
    return float(unit_pnl) * float(amount)


def book_stats(ledger: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    closed = [r for r in ledger["trades"].values() if _closed(r)]
    closed.sort(key=lambda r: r.get("settle_time") or r.get("timestamp") or "")
    opens = [
        r
        for r in ledger["trades"].values()
        if not r.get("void") and r.get("pnl") is None
    ]
    opens.sort(key=lambda r: r.get("timestamp") or "")

    def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {
                "N": 0,
                "win_rate": None,
                "EV": None,
                "pnl_sum": 0.0,
                "EV_usdt": None,
                "pnl_usdt": 0.0,
                "maxLL": 0,
            }
        pnls = [float(r["pnl"]) for r in rows]
        usdts = [float(r["pnl"]) * trade_amount(r) for r in rows]
        streak = best = 0
        for x in pnls:
            if x < 0:
                streak += 1
                best = max(best, streak)
            else:
                streak = 0
        wins = sum(1 for x in pnls if x > 0)
        return {
            "N": n,
            "win_rate": wins / n,
            "EV": sum(pnls) / n,
            "pnl_sum": float(sum(pnls)),
            "EV_usdt": float(sum(usdts) / n),
            "pnl_usdt": float(sum(usdts)),
            "maxLL": int(best),
        }

    by_sym = {}
    for sym in SYMBOLS:
        by_sym[sym] = _agg([r for r in closed if r.get("symbol") == sym])

    return {
        "now": now.isoformat(),
        "all": _agg(closed),
        "today": _agg([r for r in closed if _in_range(r.get("settle_time"), day0, day0 + timedelta(days=1))]),
        "month": _agg([r for r in closed if _in_range(r.get("settle_time"), month0, now + timedelta(days=1))]),
        "by_symbol": by_sym,
        "open": opens,
        "amount_default": trade_amount(),
    }


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{100.0 * x:.2f}%"


def _fmt_pnl(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.2f}"


def _fmt_usdt(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.2f}U"

def _side(bias: int) -> str:
    if bias > 0:
        return "LONG"
    if bias < 0:
        return "SHORT"
    return "FLAT"


def _side_emoji(bias: int) -> str:
    if bias > 0:
        return "🟢"
    if bias < 0:
        return "🔴"
    return "⚪"


def format_stats_block(st: dict[str, Any]) -> str:
    a, d, m = st["all"], st["today"], st["month"]
    lines = [
        f"账本  N={a['N']}  胜率={_fmt_pct(a['win_rate'])}  EV={_fmt_usdt(a.get('EV_usdt'))}  累计={_fmt_usdt(a.get('pnl_usdt'))}  最长连亏={a['maxLL']}",
        f"今日  N={d['N']}  胜率={_fmt_pct(d['win_rate'])}  累计={_fmt_usdt(d.get('pnl_usdt'))}",
        f"本月  N={m['N']}  胜率={_fmt_pct(m['win_rate'])}  累计={_fmt_usdt(m.get('pnl_usdt'))}",
    ]
    for sym, s in st["by_symbol"].items():
        if s["N"] == 0:
            continue
        lines.append(
            f"{html_escape(sym)}  N={s['N']}  胜率={_fmt_pct(s['win_rate'])}  累计={_fmt_usdt(s.get('pnl_usdt'))}"
        )
    if st["open"]:
        bits = []
        for r in st["open"]:
            bits.append(f"{r['symbol']} {_side(int(r['bias']))} @{r.get('timestamp')}")
        lines.append("持仓中：" + "；".join(bits))
    else:
        lines.append("持仓中：无")
    return "\n".join(lines)


def format_entry(rec: dict[str, Any]) -> str:
    bias = int(rec["bias"])
    side = _side(bias)
    track = rec.get("bb_lower_k") if bias > 0 else rec.get("bb_upper_k")
    rsi = rec.get("rsi")
    rsi_s = f"{rsi:.2f}" if rsi is not None else "n/a"
    track_s = f"{track:.2f}" if track is not None else "n/a"
    entry_s = f"{rec['entry']:.2f}" if rec.get("entry") is not None else "n/a"
    extra = ""
    cb = rec.get("copybot")
    if isinstance(cb, dict):
        if cb.get("dry_run"):
            extra = f"\n下单 dry-run {cb.get('payload', {}).get('symbolName')} {cb.get('payload', {}).get('direction')} {cb.get('payload', {}).get('orderAmount')}U"
        elif cb.get("ok"):
            extra = (
                f"\n已下单 {html_escape(str(cb.get('payload', {}).get('symbolName')))} "
                f"{html_escape(str(cb.get('payload', {}).get('direction')))} "
                f"{html_escape(str(cb.get('payload', {}).get('orderAmount')))}U"
            )
        elif cb.get("skipped"):
            extra = f"\n未下单：{html_escape(str(cb.get('reason')))}"
        elif cb.get("error"):
            extra = f"\n下单失败：{html_escape(str(cb.get('error'))[:200])}"
    return (
        f"{_side_emoji(bias)} <b>RSI_BB 开仓 {html_escape(side)}</b>\n"
        f"<code>{html_escape(rec['symbol'])}</code>\n"
        f"入场 {_iso(rec['timestamp'])}  @{entry_s}\n"
        f"RSI({RSI_BB_PERIOD})={rsi_s}  轨={track_s}\n"
        f"结算 {_iso(rec.get('settle_time'))}\n"
        f"规则 RSI {RSI_BB_OS:.0f}/{RSI_BB_OB:.0f} k={RSI_BB_K}  持仓 {HOLD_MINUTES}min\n"
        f"跳过 {SKIP_SESSION_BJ_LABEL}"
        f"{extra}"
    )


def format_settle(rec: dict[str, Any], st: dict[str, Any]) -> str:
    pnl = rec.get("pnl")
    amt = trade_amount(rec)
    usdt = unit_to_usdt(float(pnl), amt) if pnl is not None else None
    if rec.get("void"):
        head = "⚠️ <b>结算作废</b>（缺 1m）"
    elif pnl is None:
        head = "⚠️ <b>结算未知</b>"
    elif pnl > 0:
        head = f"✅ <b>WIN {_fmt_usdt(usdt)}</b> ({_fmt_pnl(pnl)} × {amt:g}U)"
    elif pnl < 0:
        head = f"❌ <b>LOSS {_fmt_usdt(usdt)}</b> ({_fmt_pnl(pnl)} × {amt:g}U)"
    else:
        head = f"➖ <b>TIE {_fmt_usdt(0.0)}</b>"
    entry_s = f"{rec['entry']:.2f}" if rec.get("entry") is not None else "n/a"
    settle_s = f"{rec['settle']:.2f}" if rec.get("settle") is not None else "n/a"
    return (
        f"{head}\n"
        f"<code>{html_escape(rec['symbol'])}</code> {_side(int(rec['bias']))}\n"
        f"{entry_s} → {settle_s}\n"
        f"{_iso(rec['timestamp'])} → {_iso(rec.get('settle_time'))}\n"
        f"\n{format_stats_block(st)}"
    )


def format_daily(st: dict[str, Any], day: str) -> str:
    return (
        f"📊 <b>RSI_BB 日报 {html_escape(day)} UTC</b>\n"
        f"规则 RSI({RSI_BB_PERIOD}) {RSI_BB_OS:.0f}/{RSI_BB_OB:.0f} k={RSI_BB_K}\n"
        f"\n{format_stats_block(st)}"
    )


def scan_live(lookback_minutes: int = LOOKBACK_MINUTES, now: datetime | None = None) -> pd.DataFrame:
    frames = []
    for symbol in SYMBOLS:
        log(f"[live] {symbol}")
        df_1m = fetch_recent_1m(symbol, lookback_minutes=lookback_minutes, now=now)
        if df_1m.empty:
            log(f"[live] {symbol} 无 K 线")
            continue
        trades, _ = scan_rsi_bb(df_1m, symbol)
        frames.append(trades)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    return out


def maybe_daily(ledger: dict[str, Any], now: datetime) -> tuple[str | None, str]:
    """返回 (日报正文或 None, 应写入的 last_daily_utc)。发送成功后再落盘日期。"""
    today = now.date().isoformat()
    prev = ledger.get("last_daily_utc")
    if prev is None:
        return None, today
    if prev >= today:
        return None, today
    y0 = datetime.fromisoformat(prev)
    if y0.tzinfo is None:
        y0 = y0.replace(tzinfo=timezone.utc)
    y1 = y0 + timedelta(days=1)
    st = book_stats(ledger, now)
    y_closed = [r for r in ledger["trades"].values() if _closed(r) and _in_range(r.get("settle_time"), y0, y1)]
    pnls = [float(r["pnl"]) for r in y_closed]
    n = len(pnls)
    wr = (sum(1 for x in pnls if x > 0) / n) if n else None
    ev = (sum(pnls) / n) if n else None
    usdt_sum = sum(float(r["pnl"]) * trade_amount(r) for r in y_closed) if n else 0.0
    ev_u = (usdt_sum / n) if n else None
    extra = (
        f"昨日结算  N={n}  胜率={_fmt_pct(wr)}  EV={_fmt_usdt(ev_u)}  "
        f"累计={_fmt_usdt(usdt_sum)}（单位EV={_fmt_pnl(ev)}）"
    )
    return format_daily(st, prev) + "\n" + extra, today


def auto_lookback(ledger: dict[str, Any]) -> int:
    return LOOKBACK_MINUTES if not ledger.get("trades") else WARM_LOOKBACK_MINUTES


def run_once(
    *,
    dry_run: bool = False,
    lookback_minutes: int | None = None,
    now: datetime | None = None,
    trades: pd.DataFrame | None = None,
    save: bool = True,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    ledger = load_ledger()
    if trades is None:
        lb = lookback_minutes if lookback_minutes is not None else auto_lookback(ledger)
        log(f"[live] lookback={lb}min")
        trades = scan_live(lookback_minutes=lb, now=now)
    events = ingest_trades(ledger, trades, now)
    daily_msg, daily_stamp = maybe_daily(ledger, now)
    messages: list[str] = []
    if daily_msg:
        messages.append(daily_msg)
    for kind, rec in events:
        if kind == "entry":
            messages.append(format_entry(rec))
        else:
            messages.append(format_settle(rec, book_stats(ledger, now)))
    if save:
        save_ledger(ledger)
    if daily_msg:
        send_telegram(daily_msg, dry_run=dry_run)
        ledger["last_daily_utc"] = daily_stamp
        if save:
            save_ledger(ledger)
        log(f"[tg] sent {daily_msg.splitlines()[0][:80]}")
    elif ledger.get("last_daily_utc") is None:
        ledger["last_daily_utc"] = daily_stamp
        if save:
            save_ledger(ledger)
    for kind, rec in events:
        if kind == "entry":
            rec["copybot"] = place_for_signal(rec, dry_run=dry_run)
            if save:
                save_ledger(ledger)
            msg = format_entry(rec)
            send_telegram(msg, dry_run=dry_run)
            rec["notified_entry"] = True
        else:
            msg = format_settle(rec, book_stats(ledger, now))
            send_telegram(msg, dry_run=dry_run)
            rec["notified_settle"] = True
        if save:
            save_ledger(ledger)
        log(f"[tg] sent {msg.splitlines()[0][:80]}")
    if not messages:
        n_open = sum(
            1
            for r in ledger["trades"].values()
            if not r.get("void") and r.get("pnl") is None
        )
        extra = f"  持仓中={n_open}" if n_open else ""
        log(f"[live] 本轮无新开仓/结算{extra}")
    return messages


def seconds_to_next_5m(now: datetime | None = None, lag_sec: int = BAR_LAG_SEC) -> float:
    now = now or datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    nxt = (epoch // FIVE_MIN + 1) * FIVE_MIN + lag_sec
    return max(1.0, float(nxt - now.timestamp()))


def run_loop(*, dry_run: bool = False, lookback_minutes: int | None = None) -> None:
    stop = {"flag": False}

    def _stop(*_a: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log(
        f"RSI_BB 实盘循环  RSI({RSI_BB_PERIOD}) {RSI_BB_OS:.0f}/{RSI_BB_OB:.0f} k={RSI_BB_K}  "
        f"跳过{SKIP_SESSION_BJ_LABEL}"
    )
    run_once(dry_run=dry_run, lookback_minutes=lookback_minutes)
    while not stop["flag"]:
        wait = seconds_to_next_5m()
        log(f"[live] sleep {wait:.0f}s → 下一根 5m")
        end = time.time() + wait
        while time.time() < end and not stop["flag"]:
            time.sleep(min(1.0, end - time.time()))
        if stop["flag"]:
            break
        try:
            run_once(dry_run=dry_run, lookback_minutes=lookback_minutes)
        except Exception as exc:  # noqa: BLE001
            log(f"[live] 本轮失败: {exc}", err=True)
            send_telegram(f"⚠️ RSI_BB 扫描失败：{html_escape(str(exc)[:300])}", dry_run=dry_run)
    log("[live] 已停止")


def main() -> None:
    ap = argparse.ArgumentParser(description="RSI_BB 信号推 Telegram + 盈亏账本")
    ap.add_argument("--once", action="store_true", help="扫一轮后退出")
    ap.add_argument("--loop", action="store_true", help="按 5m 收盘循环")
    ap.add_argument("--test", action="store_true", help="发送一条连通测试")
    ap.add_argument("--test-copybot", action="store_true", help="检查跟单 webhook 配置，不下单")
    ap.add_argument("--stats", action="store_true", help="只推送当前账本统计")
    ap.add_argument("--dry-run", action="store_true", help="打印消息，不调用 Telegram / 不下真实单")
    ap.add_argument("--lookback", type=int, default=None, help="REST 回看分钟数（默认：首次 7 天，之后 36 小时）")
    args = ap.parse_args()

    if args.test:
        send_telegram(
            f"RSI_BB 连通测试\n规则 RSI({RSI_BB_PERIOD}) {RSI_BB_OS:.0f}/{RSI_BB_OB:.0f} k={RSI_BB_K}\n"
            f"跳过 {SKIP_SESSION_BJ_LABEL}",
            dry_run=args.dry_run,
        )
        log("测试消息已处理。")
        return
    if args.test_copybot:
        bot = get_client()
        token = copybot_token()
        token_s = f"{token[:6]}…{token[-4:]}" if len(token) >= 12 else ("已配置" if token else "空")
        try:
            status = bot.ping()
        except Exception as exc:  # noqa: BLE001
            status = f"失败 {exc}"
        log(
            f"跟单 webhook url={bot.webhook_url()} token={token_s} "
            f"amount={copybot_amount()} enabled={copybot_enabled()} ping={status}"
        )
        return
    if args.stats:
        st = book_stats(load_ledger())
        msg = (
            f"📊 <b>RSI_BB 账本</b>\n"
            f"规则 RSI({RSI_BB_PERIOD}) {RSI_BB_OS:.0f}/{RSI_BB_OB:.0f} k={RSI_BB_K}\n"
            f"\n{format_stats_block(st)}"
        )
        send_telegram(msg, dry_run=args.dry_run)
        log(format_stats_block(st))
        return
    if args.loop:
        run_loop(dry_run=args.dry_run, lookback_minutes=args.lookback)
        return
    if not args.once:
        log("未指定模式，默认 --once。长期推送请用 --loop。")
    run_once(dry_run=args.dry_run, lookback_minutes=args.lookback)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        log(str(exc), err=True)
        raise SystemExit(1)
