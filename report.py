"""
统计、Markdown 总结与落盘。OOS 结论口径固定，禁止用 OOS 改参。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import EV_WINRATE_THRESHOLD

IS_START = datetime(2023, 1, 1, tzinfo=timezone.utc)
IS_END = datetime(2025, 1, 1, tzinfo=timezone.utc)
OOS_START = datetime(2025, 1, 1, tzinfo=timezone.utc)
OOS_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
BOOTSTRAP_N = 5000
BOOTSTRAP_SEED = 42
AUDIT_SEED = 42
AUDIT_N = 10

PERIODS = {
    "IS": (pd.Timestamp(IS_START), pd.Timestamp(IS_END)),
    "OOS": (pd.Timestamp(OOS_START), pd.Timestamp(OOS_END)),
    "FULL": (pd.Timestamp(IS_START), pd.Timestamp(OOS_END)),
}


def _slice_period(trades: pd.DataFrame, period: str) -> pd.DataFrame:
    lo, hi = PERIODS[period]
    ts = pd.to_datetime(trades["timestamp"], utc=True)
    return trades.loc[(ts >= lo) & (ts < hi)].copy()


def _max_drawdown(pnl: np.ndarray) -> float:
    if pnl.size == 0:
        return float("nan")
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    return float(dd.min()) if dd.size else 0.0


def _max_losing_streak(pnl: np.ndarray) -> int:
    if pnl.size == 0:
        return 0
    best = cur = 0
    for x in pnl:
        if x < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def _bootstrap_ev(pnl: np.ndarray, n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED) -> dict:
    if pnl.size == 0:
        return {"p5": None, "p50": None, "p95": None, "n": 0}
    rng = np.random.default_rng(seed)
    # 分块抽样避免超大矩阵
    evs = np.empty(n, dtype=np.float64)
    m = pnl.size
    chunk = 50
    pos = 0
    while pos < n:
        k = min(chunk, n - pos)
        idx = rng.integers(0, m, size=(k, m))
        evs[pos : pos + k] = pnl[idx].mean(axis=1)
        pos += k
    return {
        "p5": float(np.quantile(evs, 0.05)),
        "p50": float(np.quantile(evs, 0.50)),
        "p95": float(np.quantile(evs, 0.95)),
        "n": int(n),
    }


def _hour_weekday_tables(valid: pd.DataFrame) -> dict:
    ts = pd.to_datetime(valid["timestamp"], utc=True)
    tmp = valid.copy()
    tmp["hour"] = ts.dt.hour
    tmp["weekday"] = ts.dt.dayofweek
    hours = {}
    for h in range(24):
        sub = tmp.loc[tmp["hour"] == h]
        n = len(sub)
        wr = float((sub["pnl"] > 0).mean()) if n else None
        ev = float(sub["pnl"].mean()) if n else None
        hours[str(h)] = {"N": n, "win_rate": wr, "EV": ev}
    weekdays = {}
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d in range(7):
        sub = tmp.loc[tmp["weekday"] == d]
        n = len(sub)
        wr = float((sub["pnl"] > 0).mean()) if n else None
        ev = float(sub["pnl"].mean()) if n else None
        weekdays[names[d]] = {"N": n, "win_rate": wr, "EV": ev}
    return {"by_hour": hours, "by_weekday": weekdays}


def metrics_one(trades: pd.DataFrame) -> dict:
    """对一组已筛好的交易算全部统计。void 不计入 N。"""
    if trades.empty:
        return {
            "N": 0,
            "n_void": 0,
            "void_rate": None,
            "win_rate": None,
            "EV": None,
            "payoff_ratio": None,
            "max_drawdown": None,
            "max_losing_streak": None,
            "threshold": EV_WINRATE_THRESHOLD,
            "above_threshold": None,
            "bootstrap": _bootstrap_ev(np.array([])),
            "by_hour": {str(h): {"N": 0, "win_rate": None, "EV": None} for h in range(24)},
            "by_weekday": {},
        }
    n_attempt = int(len(trades))
    n_void = int(trades["void"].fillna(False).sum()) if "void" in trades.columns else 0
    valid = trades.loc[~trades["void"].fillna(False) & trades["pnl"].notna()].copy()
    n = int(len(valid))
    void_rate = (n_void / n_attempt) if n_attempt else None
    if n == 0:
        out = metrics_one(pd.DataFrame())
        out["n_void"] = n_void
        out["void_rate"] = void_rate
        return out
    pnl = valid["pnl"].to_numpy(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    win_rate = float((pnl > 0).mean())  # 平局计入 N，不计入胜
    ev = float(pnl.mean())
    payoff_ratio = float(wins.mean() / np.abs(losses.mean())) if wins.size and losses.size else None
    boot = _bootstrap_ev(pnl)
    cal = _hour_weekday_tables(valid)
    return {
        "N": n,
        "n_void": n_void,
        "void_rate": void_rate,
        "win_rate": win_rate,
        "EV": ev,
        "payoff_ratio": payoff_ratio,
        "max_drawdown": _max_drawdown(pnl),
        "max_losing_streak": _max_losing_streak(pnl),
        "threshold": EV_WINRATE_THRESHOLD,
        "above_threshold": bool(win_rate > EV_WINRATE_THRESHOLD),
        "bootstrap": boot,
        **cal,
    }


def build_metrics(trades: pd.DataFrame) -> dict:
    out: dict = {}
    if trades.empty:
        return out
    strategies = sorted(trades["strategy"].dropna().unique())
    symbols = sorted(trades["symbol"].dropna().unique())
    for strat in strategies:
        out[strat] = {}
        ts = trades.loc[trades["strategy"] == strat]
        setups = ["ALL"]
        if strat == "RFA30":
            setups = ["TREND_PULLBACK", "RANGE_FADE", "ALL"]
        for sym in list(symbols) + ["BOTH"]:
            sub_sym = ts if sym == "BOTH" else ts.loc[ts["symbol"] == sym]
            out[strat][sym] = {}
            for setup in setups:
                piece = sub_sym if setup == "ALL" else sub_sym.loc[sub_sym["setup"] == setup]
                out[strat][sym][setup] = {}
                for period in ("IS", "OOS", "FULL"):
                    out[strat][sym][setup][period] = metrics_one(_slice_period(piece, period))
    return out


def pick_audit_trades(trades: pd.DataFrame, n: int = AUDIT_N, seed: int = AUDIT_SEED) -> pd.DataFrame:
    if trades.empty:
        return trades
    void = trades["void"].fillna(False) if "void" in trades.columns else False
    valid = trades.loc[~void & trades["pnl"].notna()]
    if valid.empty:
        return valid
    k = min(n, len(valid))
    return valid.sample(n=k, random_state=seed).sort_values("timestamp")


def _df_to_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if pd.isna(v):
                cells.append("")
            elif isinstance(v, pd.Timestamp):
                cells.append(v.isoformat())
            elif isinstance(v, float):
                cells.append(f"{v:.6g}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{100.0 * x:.2f}%"


def _fmt_num(x, nd=4) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def _oos_loss_slices(trades: pd.DataFrame, strategy: str = "RFA30") -> list[str]:
    """OOS 主要亏损切片：小时、setup、币种。"""
    oos = _slice_period(trades.loc[trades["strategy"] == strategy], "OOS")
    valid = oos.loc[~oos["void"].fillna(False) & oos["pnl"].notna()].copy()
    lines = []
    if valid.empty:
        return ["OOS 无有效成交，无法列出亏损切片。"]
    for col, name in (("symbol", "币种"), ("setup", "setup")):
        g = valid.groupby(col)["pnl"].agg(["count", "mean", "sum"])
        g = g.sort_values("mean")
        for idx, row in g.iterrows():
            lines.append(
                f"- {name}={idx}: N={int(row['count'])} EV={row['mean']:.4f} sum={row['sum']:.2f}"
            )
    ts = pd.to_datetime(valid["timestamp"], utc=True)
    valid = valid.copy()
    valid["hour"] = ts.dt.hour
    gh = valid.groupby("hour")["pnl"].agg(["count", "mean", "sum"]).sort_values("mean")
    worst = gh.head(5)
    lines.append("- UTC 小时最差 5 档:")
    for idx, row in worst.iterrows():
        lines.append(f"  - hour={int(idx)}: N={int(row['count'])} EV={row['mean']:.4f} sum={row['sum']:.2f}")
    return lines


def _judge_oos(metrics: dict, trades: pd.DataFrame, strategy: str) -> list[str]:
    """同一套 OOS 门槛：胜率<56% 或 EV<=0 或 bootstrap 5%分位<=0 → 无可用优势。"""
    lines = []
    both = metrics.get(strategy, {}).get("BOTH", {}).get("ALL", {}).get("OOS", {})
    wr = both.get("win_rate")
    ev = both.get("EV")
    p5 = (both.get("bootstrap") or {}).get("p5")
    n = both.get("N") or 0
    lines.append(
        f"{strategy} OOS 合计（BOTH / ALL）：N={n} win_rate={_fmt_pct(wr)} EV={_fmt_num(ev)} bootstrap_p5={_fmt_num(p5)}"
    )
    fail = (wr is None) or (wr < 0.56) or (ev is None) or (ev <= 0) or (p5 is None) or (p5 <= 0)
    if p5 is not None and p5 <= 0:
        lines.append("OOS bootstrap 5% 分位 <= 0：**统计上不稳健**。")
    if fail:
        lines.append(f"**结论：{strategy} 在样本外没有可用优势。**")
        lines.append("未建议实盘。主要亏损切片：")
        lines.extend(_oos_loss_slices(trades, strategy))
    else:
        lines.append("OOS 通过预先设定的可用门槛（胜率>=56% 且 EV>0 且 bootstrap 5%分位>0）。")
        lines.append("**只建议：保持原参数，实盘小资金记录。禁止改阈值。**")
    return lines


def _conclusion(metrics: dict, trades: pd.DataFrame) -> list[str]:
    lines = ["### RFA-30（原规则，未改参）", ""]
    lines.extend(_judge_oos(metrics, trades, "RFA30"))
    if "RSI_BB" in metrics:
        lines += ["", "### RSI_BB（IS 冻结：RSI(7) 20/80 + 布林 k=2.2；OOS 未用于改参）", ""]
        lines.extend(_judge_oos(metrics, trades, "RSI_BB"))
    return lines


def _md_table_block(metrics: dict, strat: str, setup: str) -> str:
    rows = []
    header = (
        "| symbol | period | N | win_rate | EV | payoff | maxDD | maxLL | void_rate | above_54.05% | boot p5/p50/p95 |\n"
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"
    )
    rows.append(header)
    block = metrics.get(strat, {})
    for sym in block:
        if setup not in block[sym]:
            continue
        for period in ("IS", "OOS", "FULL"):
            m = block[sym][setup][period]
            boot = m.get("bootstrap") or {}
            boot_s = f"{_fmt_num(boot.get('p5'))}/{_fmt_num(boot.get('p50'))}/{_fmt_num(boot.get('p95'))}"
            rows.append(
                f"| {sym} | {period} | {m.get('N', 0)} | {_fmt_pct(m.get('win_rate'))} | {_fmt_num(m.get('EV'))} | "
                f"{_fmt_num(m.get('payoff_ratio'))} | {_fmt_num(m.get('max_drawdown'))} | {m.get('max_losing_streak')} | "
                f"{_fmt_pct(m.get('void_rate'))} | {m.get('above_threshold')} | {boot_s} |"
            )
    return "\n".join(rows)


def _hour_md(m: dict) -> str:
    by = m.get("by_hour") or {}
    parts = ["| hour | N | win_rate | EV |", "|---:|---:|---:|---:|"]
    for h in range(24):
        cell = by.get(str(h), {})
        parts.append(f"| {h:02d} | {cell.get('N', 0)} | {_fmt_pct(cell.get('win_rate'))} | {_fmt_num(cell.get('EV'))} |")
    return "\n".join(parts)


def _weekday_md(m: dict) -> str:
    by = m.get("by_weekday") or {}
    parts = ["| weekday | N | win_rate | EV |", "|---|---:|---:|---:|"]
    for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        cell = by.get(name, {})
        parts.append(f"| {name} | {cell.get('N', 0)} | {_fmt_pct(cell.get('win_rate'))} | {_fmt_num(cell.get('EV'))} |")
    return "\n".join(parts)


def write_markdown(metrics: dict, audit: pd.DataFrame, trades: pd.DataFrame) -> str:
    lines = [
        "# RFA-30 事件合约回放报告",
        "",
        "RFA-30 规则与参数按规格固定，未改。",
        "RSI_BB：15m RSI + 布林轨同时满足才开。阈值只在 IS 网格上按「适中 N + 最高胜率」冻结，OOS 只确认一次。",
        "IS=2023-01-01~2024-12-31 UTC，OOS=2025-01-01~2026-09-01 UTC。",
        "未使用 OOS 调参。赔率赢 +0.85 / 输 -1.0；手续费滑点不再另扣。",
        f"正期望最低胜率阈值 = 1/1.85 ≈ {EV_WINRATE_THRESHOLD:.6%}。",
        "",
        "## 结论",
        "",
    ]
    lines.extend(_conclusion(metrics, trades))
    lines += ["", "## 对照总表（setup=ALL）", ""]
    for strat in ("RFA30", "RSI_BB", "BASE_LONG", "BASE_MOM", "BASE_1H"):
        if strat not in metrics:
            continue
        lines += [f"### {strat}", "", _md_table_block(metrics, strat, "ALL"), ""]
    if "RFA30" in metrics:
        lines += ["## RFA-30 分 setup", ""]
        for setup in ("TREND_PULLBACK", "RANGE_FADE"):
            lines += [f"### {setup}", "", _md_table_block(metrics, "RFA30", setup), ""]

        both_oos = metrics["RFA30"].get("BOTH", {}).get("ALL", {}).get("OOS", {})
        lines += ["## OOS 小时 / 星期（RFA30 BOTH ALL）", "", "### UTC 小时", "", _hour_md(both_oos), ""]
        lines += ["### 星期", "", _weekday_md(both_oos), ""]
    if "RSI_BB" in metrics:
        s_oos = metrics["RSI_BB"].get("BOTH", {}).get("ALL", {}).get("OOS", {})
        lines += ["## OOS 小时 / 星期（RSI_BB BOTH ALL）", "", "### UTC 小时", "", _hour_md(s_oos), ""]
        lines += ["### 星期", "", _weekday_md(s_oos), ""]

        # 分标的 OOS 小时表过长，仍输出 IS/OOS/FULL 的 BOTH 小时会很大；规格要求每个 symbol/setup/period 都有小时与星期。
        lines += ["## 分标的、分 setup 的小时与星期", ""]
        for sym, setups in metrics["RFA30"].items():
            for setup, periods in setups.items():
                for period, m in periods.items():
                    lines += [f"### RFA30 {sym} {setup} {period}", "", _hour_md(m), "", _weekday_md(m), ""]

    lines += ["## 随机抽 10 笔 RSI_BB 交易（人工核对）", ""]
    if audit is None or audit.empty:
        lines.append("无成交可抽。")
    else:
        show_cols = [
            "timestamp",
            "symbol",
            "regime",
            "setup",
            "bias",
            "quality",
            "entry",
            "settle",
            "pnl",
            "h1_close",
            "h1_ema20",
            "h1_ema50",
            "h1_adx",
            "m15_close",
            "m15_ema20",
            "m15_ema50",
            "m15_atr14",
            "m15_rsi14",
            "m15_rsi7",
            "m15_bb_upper",
            "m15_bb_lower",
            "vwap",
            "close",
            "ema8_5",
            "atr14_5",
            "tr5",
        ]
        have = [c for c in show_cols if c in audit.columns]
        lines.append(_df_to_md(audit[have]))
        lines.append("")
        lines.append("核对要点：RSI_BB 为 15m RSI 与布林轨同时触及；结算为 T+30min 的 1m close。")

    search_csv = Path(__file__).resolve().parent / "output" / "rsi_bb_search.csv"
    winner_js = Path(__file__).resolve().parent / "output" / "rsi_bb_winner.json"
    if search_csv.exists():
        g = pd.read_csv(search_csv)
        lines += ["", "## RSI_BB IS 网格（挑选未看 OOS）", ""]
        if winner_js.exists():
            w = json.loads(winner_js.read_text(encoding="utf-8"))
            lines.append(
                f"冻结：RSI({w.get('rsi_period')}) {w.get('rsi_os')}/{w.get('rsi_ob')}，"
                f"布林 k={w.get('bb_k')}；IS N={w.get('IS_N')} 胜率={w.get('IS_win_rate')}。"
            )
        lines.append("规则：400≤N≤4000 且双币各≥150，再取 IS 胜率最高。")
        show = g.sort_values("win_rate", ascending=False).head(10)
        lines.append(_df_to_md(show))

    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "python -m pip install -r requirements.txt",
        "python download.py && python search_rsi_bb.py && python backtest.py",
        "python -m pytest tests/test_rfa30.py -q",
        "```",
        "",
    ]
    return "\n".join(lines)


def save_outputs(
    out_dir: Path,
    trades: pd.DataFrame,
    metrics: dict,
    equity: pd.DataFrame,
    markdown: str,
    signals: pd.DataFrame | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv", index=False)
    equity.to_csv(out_dir / "equity_curve.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    (out_dir / "REPORT.md").write_text(markdown, encoding="utf-8")
    if signals is not None and not signals.empty:
        signals.to_csv(out_dir / "signals.csv", index=False)
    readme = out_dir.parent / "README_RUN.md"
    if readme.exists():
        (out_dir / "README_RUN.md").write_text(readme.read_text(encoding="utf-8"), encoding="utf-8")
