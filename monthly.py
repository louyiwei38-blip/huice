"""
RSI_BB 逐月回放统计。默认「今天往前 24 个完整自然月」。
当前样本截止 2026-09-01，故窗口为 2024-09-01 ≤ T < 2026-09-01。
不改阈值；成交来自与 detect/backtest 相同的 scan。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from report import IS_END, OOS_END
from rsi_bb import valid_trades
from strategy import EV_WINRATE_THRESHOLD, RSI_BB_K, RSI_BB_OB, RSI_BB_OS, RSI_BB_PERIOD

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"
TRADES_CSV = OUT_DIR / "trades.csv"
# 过去两年：24 个完整月，对齐回放截止日
MONTH_START = pd.Timestamp("2024-09-01", tz="UTC")
MONTH_END = pd.Timestamp(OOS_END)  # 2026-09-01


def _load_rsi_bb() -> pd.DataFrame:
    for path in (OUT_DIR / "detect_signals.csv", TRADES_CSV):
        if not path.exists():
            continue
        df = pd.read_csv(path, low_memory=False)
        if "strategy" in df.columns:
            df = df.loc[df["strategy"] == "RSI_BB"].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        v = valid_trades(df)
        if not v.empty:
            return v
    raise FileNotFoundError("缺少 detect_signals.csv 或 trades.csv，请先 python detect.py")


def _month_stats(g: pd.DataFrame) -> pd.Series:
    n = int(len(g))
    if n == 0:
        return pd.Series({"N": 0, "win_rate": None, "EV": None, "pnl_sum": 0.0, "max_losing_streak": 0})
    pnl = g.sort_values("timestamp")["pnl"].to_numpy(dtype=float)
    streak = best = 0
    for x in pnl:
        if x < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return pd.Series(
        {
            "N": n,
            "win_rate": float((pnl > 0).mean()),
            "EV": float(pnl.mean()),
            "pnl_sum": float(pnl.sum()),
            "max_losing_streak": int(best),
        }
    )


def monthly_table(trades: pd.DataFrame) -> pd.DataFrame:
    sub = trades.loc[(trades["timestamp"] >= MONTH_START) & (trades["timestamp"] < MONTH_END)].copy()
    sub["month"] = sub["timestamp"].dt.strftime("%Y-%m")
    rows = []
    months = pd.period_range("2024-09", "2026-08", freq="M")
    for m in months:
        key = str(m)
        chunk = sub.loc[sub["month"] == key]
        both = _month_stats(chunk)
        btc = _month_stats(chunk.loc[chunk["symbol"] == "BTCUSDT"])
        eth = _month_stats(chunk.loc[chunk["symbol"] == "ETHUSDT"])
        is_oos = "OOS" if pd.Timestamp(f"{key}-01", tz="UTC") >= pd.Timestamp(IS_END) else "IS"
        rows.append(
            {
                "month": key,
                "split": is_oos,
                "N": int(both["N"]),
                "win_rate": both["win_rate"],
                "EV": both["EV"],
                "pnl_sum": both["pnl_sum"],
                "maxLL": both["max_losing_streak"],
                "N_BTC": int(btc["N"]),
                "wr_BTC": btc["win_rate"],
                "EV_BTC": btc["EV"],
                "N_ETH": int(eth["N"]),
                "wr_ETH": eth["win_rate"],
                "EV_ETH": eth["EV"],
            }
        )
    return pd.DataFrame(rows)


def _fmt_pct(x) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{100.0 * float(x):.2f}%"


def _fmt_num(x, nd=4) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{float(x):.{nd}f}"


def to_markdown(tbl: pd.DataFrame, trades: pd.DataFrame) -> str:
    win = tbl.loc[tbl["N"] > 0]
    n = int(tbl["N"].sum())
    pnl = float(tbl["pnl_sum"].sum())
    ev = pnl / n if n else None
    wr = None
    if n:
        sub = trades.loc[(trades["timestamp"] >= MONTH_START) & (trades["timestamp"] < MONTH_END)]
        wr = float((sub["pnl"] > 0).mean())
    lines = [
        "# RSI_BB 过去两年逐月回放",
        "",
        f"规则冻结：15m RSI({RSI_BB_PERIOD}) {RSI_BB_OS}/{RSI_BB_OB}，布林 k={RSI_BB_K}。",
        f"窗口：{MONTH_START.date()} ≤ 入场 < {MONTH_END.date()} UTC（24 个完整月）。",
        f"正期望胜率门槛 ≈ {EV_WINRATE_THRESHOLD:.2%}。未改参。",
        "",
        f"**两年合计** N={n} 胜率={_fmt_pct(wr)} EV={_fmt_num(ev)} 累计pnl={_fmt_num(pnl, 2)}",
        "",
        "| month | split | N | win_rate | EV | pnl_sum | maxLL | N_BTC | wr_BTC | EV_BTC | N_ETH | wr_ETH | EV_ETH |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in tbl.iterrows():
        lines.append(
            f"| {r['month']} | {r['split']} | {int(r['N'])} | {_fmt_pct(r['win_rate'])} | {_fmt_num(r['EV'])} | "
            f"{_fmt_num(r['pnl_sum'], 2)} | {int(r['maxLL'])} | {int(r['N_BTC'])} | {_fmt_pct(r['wr_BTC'])} | "
            f"{_fmt_num(r['EV_BTC'])} | {int(r['N_ETH'])} | {_fmt_pct(r['wr_ETH'])} | {_fmt_num(r['EV_ETH'])} |"
        )
    # 最差 / 最好月
    if not win.empty:
        worst = win.sort_values("EV").iloc[0]
        best = win.sort_values("EV", ascending=False).iloc[0]
        lines += [
            "",
            f"EV 最好月：{best['month']} N={int(best['N'])} wr={_fmt_pct(best['win_rate'])} EV={_fmt_num(best['EV'])}",
            f"EV 最差月：{worst['month']} N={int(worst['N'])} wr={_fmt_pct(worst['win_rate'])} EV={_fmt_num(worst['EV'])}",
        ]
    return "\n".join(lines)


def main() -> None:
    trades = _load_rsi_bb()
    tbl = monthly_table(trades)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tbl.to_csv(OUT_DIR / "monthly.csv", index=False)
    md = to_markdown(tbl, trades)
    (OUT_DIR / "MONTHLY.md").write_text(md + "\n", encoding="utf-8")
    print(md)
    print(f"\n写出 {OUT_DIR / 'monthly.csv'}  {OUT_DIR / 'MONTHLY.md'}")


if __name__ == "__main__":
    main()
