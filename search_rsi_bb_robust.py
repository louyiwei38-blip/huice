"""
多种 IS 方法重选 RSI+布林阈值。胜率保持：IS 合计胜率 >= 58%。
全部挑选只看 IS；OOS 只在选定后打印，不参与打分。

方法（均在 400–4000 笔、双币各 >=150 的池子里）：
  A 最大合计胜率（旧规则）
  B 最大 min(2023胜率, 2024胜率)     — 年份切分稳健
  C 最大 min(BTC胜率, ETH胜率)       — 标的稳健
  D 最大月胜率中位数（月内 N>=8）     — 月份稳健
  E 最大胜率 bootstrap 5% 分位       — 统计下限
  F 只在 k∈{1.8,2.0,2.2} 中取胜率最高 — 靠近教科书布林 2σ，避免贴网格边

综合（预先写死）：在 IS 胜率>=58% 的合格组合中
  score = min(wr_2023,wr_2024) + min(wr_btc,wr_eth) + 月胜率中位数
  取 score 最高；并列先取 |k-2.0| 更小，再取 N 更接近 1500。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from download import END, START, SYMBOLS, load_symbol_1m
from indicators import build_feature_frame, to_close_index
from report import IS_END, IS_START, OOS_END, OOS_START
from strategy import apply_position_gate, evaluate_rsi_bb, extract_trades, settle_trades

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"

RSI_PERIODS = (7, 14)
RSI_PAIRS = ((15, 85), (18, 82), (20, 80), (22, 78), (25, 75), (30, 70), (35, 65))
BB_KS = (1.5, 1.8, 2.0, 2.2, 2.5)
N_MOD_LO, N_MOD_HI = 400, 4000
N_SYM_MIN = 150
N_TARGET = 1500
WR_KEEP = 0.58  # 胜率保持
CLASSIC_K = (1.8, 2.0, 2.2)
Y2023 = (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
Y2024 = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC"))


def _valid(tr: pd.DataFrame, lo, hi) -> pd.DataFrame:
    ts = pd.to_datetime(tr["timestamp"], utc=True)
    return tr.loc[(ts >= lo) & (ts < hi) & ~tr["void"].fillna(False) & tr["pnl"].notna()].copy()


def _wr_n(v: pd.DataFrame) -> tuple[float | None, int]:
    n = int(len(v))
    if n == 0:
        return None, 0
    return float((v["pnl"].to_numpy(dtype=float) > 0).mean()), n


def _month_median_wr(v: pd.DataFrame) -> float | None:
    if v.empty:
        return None
    ts = pd.to_datetime(v["timestamp"], utc=True)
    key = ts.dt.strftime("%Y-%m")
    wrs = []
    for _, g in v.groupby(key):
        if len(g) >= 8:
            wrs.append(float((g["pnl"] > 0).mean()))
    if len(wrs) < 6:
        return None
    return float(np.median(wrs))


def _boot_wr_p5(pnl: np.ndarray, n_boot: int = 2000, seed: int = 42) -> float | None:
    if pnl.size < 30:
        return None
    wins = (pnl > 0).astype(np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, wins.size, size=(n_boot, wins.size))
    return float(np.quantile(wins[idx].mean(axis=1), 0.05))


def _run_combo(feat_map, close_map, period, os_, ob_, k) -> dict:
    parts = []
    for symbol in SYMBOLS:
        sig = evaluate_rsi_bb(feat_map[symbol], symbol, period, os_, ob_, k)
        taken = apply_position_gate(sig["bias"].to_numpy(), sig.index)
        filled = settle_trades(sig, close_map[symbol], taken)
        tr = extract_trades(filled)
        tr["symbol"] = symbol
        parts.append(tr)
    all_tr = pd.concat(parts, ignore_index=True)
    isv = _valid(all_tr, pd.Timestamp(IS_START), pd.Timestamp(IS_END))
    oos = _valid(all_tr, pd.Timestamp(OOS_START), pd.Timestamp(OOS_END))
    wr, n = _wr_n(isv)
    ev = float(isv["pnl"].mean()) if n else None
    wr_oos, n_oos = _wr_n(oos)
    ev_oos = float(oos["pnl"].mean()) if n_oos else None
    btc = isv.loc[isv["symbol"] == "BTCUSDT"]
    eth = isv.loc[isv["symbol"] == "ETHUSDT"]
    wr_btc, n_btc = _wr_n(btc)
    wr_eth, n_eth = _wr_n(eth)
    wr_23, n_23 = _wr_n(_valid(all_tr, *Y2023))
    wr_24, n_24 = _wr_n(_valid(all_tr, *Y2024))
    pnl = isv["pnl"].to_numpy(dtype=float) if n else np.array([])
    return {
        "rsi_period": period,
        "rsi_os": os_,
        "rsi_ob": ob_,
        "bb_k": k,
        "N": n,
        "win_rate": wr,
        "EV": ev,
        "N_BTC": n_btc,
        "wr_BTC": wr_btc,
        "N_ETH": n_eth,
        "wr_ETH": wr_eth,
        "wr_2023": wr_23,
        "N_2023": n_23,
        "wr_2024": wr_24,
        "N_2024": n_24,
        "wr_month_med": _month_median_wr(isv),
        "wr_boot_p5": _boot_wr_p5(pnl) if n else None,
        "OOS_N": n_oos,
        "OOS_wr": wr_oos,
        "OOS_EV": ev_oos,
    }


def _pool(grid: pd.DataFrame) -> pd.DataFrame:
    g = grid.loc[
        grid["N"].between(N_MOD_LO, N_MOD_HI)
        & (grid["N_BTC"] >= N_SYM_MIN)
        & (grid["N_ETH"] >= N_SYM_MIN)
        & grid["win_rate"].notna()
        & (grid["win_rate"] >= WR_KEEP)
        & (grid["N_2023"] >= 80)
        & (grid["N_2024"] >= 80)
    ].copy()
    return g


def _key(row: pd.Series) -> dict:
    return {
        "rsi_period": int(row["rsi_period"]),
        "rsi_os": float(row["rsi_os"]),
        "rsi_ob": float(row["rsi_ob"]),
        "bb_k": float(row["bb_k"]),
        "IS_N": int(row["N"]),
        "IS_wr": float(row["win_rate"]),
        "IS_EV": float(row["EV"]),
        "wr_2023": row["wr_2023"],
        "wr_2024": row["wr_2024"],
        "wr_BTC": row["wr_BTC"],
        "wr_ETH": row["wr_ETH"],
        "wr_month_med": row["wr_month_med"],
        "wr_boot_p5": row["wr_boot_p5"],
        "OOS_N": int(row["OOS_N"]),
        "OOS_wr": row["OOS_wr"],
        "OOS_EV": row["OOS_EV"],
    }


def pick_methods(grid: pd.DataFrame) -> dict:
    p = _pool(grid)
    if p.empty:
        raise RuntimeError("没有 IS 胜率>=58% 且样本适中的组合")
    p = p.copy()
    p["min_year"] = p[["wr_2023", "wr_2024"]].min(axis=1)
    p["min_sym"] = p[["wr_BTC", "wr_ETH"]].min(axis=1)
    p["n_dist"] = (p["N"] - N_TARGET).abs()
    p["k_dist"] = (p["bb_k"] - 2.0).abs()
    p["score"] = p["min_year"] + p["min_sym"] + p["wr_month_med"].fillna(0)

    def top(df, cols, asc):
        return df.sort_values(cols, ascending=asc).iloc[0]

    classic = p.loc[p["bb_k"].isin(CLASSIC_K)]
    if classic.empty:
        classic = p
    methods = {
        "A_max_wr": top(p, ["win_rate", "EV", "n_dist"], [False, False, True]),
        "B_year_min": top(p, ["min_year", "win_rate", "n_dist"], [False, False, True]),
        "C_symbol_min": top(p, ["min_sym", "win_rate", "n_dist"], [False, False, True]),
        "D_month_med": top(p.dropna(subset=["wr_month_med"]), ["wr_month_med", "win_rate"], [False, False]),
        "E_boot_p5": top(p.dropna(subset=["wr_boot_p5"]), ["wr_boot_p5", "win_rate"], [False, False]),
        "F_classic_k": top(classic, ["win_rate", "EV", "k_dist"], [False, False, True]),
        "META_score": top(p, ["score", "k_dist", "n_dist"], [False, True, True]),
    }
    return {name: _key(row) for name, row in methods.items()}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("加载全样本特征（挑选仍只用 IS）...", flush=True)
    feat_map, close_map = {}, {}
    for symbol in SYMBOLS:
        df_1m = load_symbol_1m(symbol, START, END)
        feat_map[symbol] = build_feature_frame(df_1m)
        close_map[symbol] = to_close_index(df_1m)["close"]
        print(f"  {symbol} 5m={len(feat_map[symbol])}", flush=True)

    rows = []
    n_combo = len(RSI_PERIODS) * len(RSI_PAIRS) * len(BB_KS)
    i = 0
    for period in RSI_PERIODS:
        for os_, ob_ in RSI_PAIRS:
            for k in BB_KS:
                i += 1
                rec = _run_combo(feat_map, close_map, period, os_, ob_, k)
                rows.append(rec)
                wr = rec["win_rate"]
                wr_s = "n/a" if wr is None else f"{100 * wr:.2f}%"
                oos_wr = rec["OOS_wr"]
                oos_s = "n/a" if oos_wr is None else f"{100 * oos_wr:.2f}%"
                print(
                    f"[{i}/{n_combo}] RSI{period} {os_}/{ob_} k={k}  "
                    f"IS N={rec['N']} wr={wr_s}  OOS wr={oos_s}",
                    flush=True,
                )

    grid = pd.DataFrame(rows)
    grid.to_csv(OUT_DIR / "rsi_bb_robust.csv", index=False)
    methods = pick_methods(grid)
    (OUT_DIR / "rsi_bb_methods.json").write_text(json.dumps(methods, indent=2, default=str), encoding="utf-8")

    print("\n===== 各方法胜者（IS 挑选；OOS 仅对照）=====", flush=True)
    for name, d in methods.items():
        is_wr = 100 * d["IS_wr"]
        y0 = 100 * d["wr_2023"]
        y1 = 100 * d["wr_2024"]
        c0 = 100 * d["wr_BTC"]
        c1 = 100 * d["wr_ETH"]
        mm = 100 * d["wr_month_med"]
        b5 = 100 * d["wr_boot_p5"]
        oos = 100 * d["OOS_wr"]
        print(
            f"{name:14s}  RSI{d['rsi_period']} {d['rsi_os']:.0f}/{d['rsi_ob']:.0f} k={d['bb_k']}  "
            f"IS wr={is_wr:.2f}% N={d['IS_N']}  "
            f"y={y0:.1f}/{y1:.1f}  coin={c0:.1f}/{c1:.1f}  "
            f"monMed={mm:.1f} boot5={b5:.1f}  OOS wr={oos:.2f}% N={d['OOS_N']}",
            flush=True,
        )
    meta = methods["META_score"]
    print("\n===== 综合冻结（只用 IS score）=====", flush=True)
    print(json.dumps(meta, indent=2, ensure_ascii=False), flush=True)
    (OUT_DIR / "rsi_bb_winner.json").write_text(
        json.dumps({**meta, "select_rule": "META_score", "note": "多种IS方法综合；OOS未用于改参"}, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
