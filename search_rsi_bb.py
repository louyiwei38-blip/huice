"""
RSI + 布林轨：只在样本内 IS 搜阈值，选完冻结后再看 OOS。

预先写死的挑选规则（改结果前已确定，不用 OOS）：
1. 入场落在 IS：2023-01-01 <= T < 2025-01-01
2. 信号适中：400 <= N_BOTH <= 4000，且 BTC/ETH 各自 N >= 150
3. 在满足 2 的组合里，取 IS 胜率最高
4. 胜率并列：取 IS EV 更高；再并列：N_BOTH 更接近 1500
5. 若无人满足 2，放宽到 200 <= N_BOTH <= 8000 后重复 3–4

网格（对称 RSI 阈值，减少乱搜）：
- RSI 周期：7 / 14（15m Wilder）
- (超卖, 超买)：(15,85) (18,82) (20,80) (22,78) (25,75) (30,70) (35,65)
- 布林 k：1.5 / 1.8 / 2.0 / 2.2 / 2.5（中轨 SMA20，ddof=0）
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from download import END, START, SYMBOLS, load_symbol_1m
from indicators import build_feature_frame, to_close_index
from report import IS_END, IS_START, OOS_END
from strategy import apply_position_gate, evaluate_rsi_bb, extract_trades, settle_trades

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"

RSI_PERIODS = (7, 14)
RSI_PAIRS = ((15, 85), (18, 82), (20, 80), (22, 78), (25, 75), (30, 70), (35, 65))
BB_KS = (1.5, 1.8, 2.0, 2.2, 2.5)
N_MOD_LO, N_MOD_HI = 400, 4000
N_SYM_MIN = 150
N_TARGET = 1500
N_RELAX_LO, N_RELAX_HI = 200, 8000


def _is_metrics(trades: pd.DataFrame) -> dict:
    ts = pd.to_datetime(trades["timestamp"], utc=True)
    lo, hi = pd.Timestamp(IS_START), pd.Timestamp(IS_END)
    sub = trades.loc[(ts >= lo) & (ts < hi) & ~trades["void"].fillna(False) & trades["pnl"].notna()]
    n = int(len(sub))
    if n == 0:
        return {"N": 0, "win_rate": None, "EV": None}
    pnl = sub["pnl"].to_numpy(dtype=float)
    return {"N": n, "win_rate": float((pnl > 0).mean()), "EV": float(pnl.mean())}


def _run_combo(feat_map: dict, close_map: dict, period: int, os_: float, ob_: float, k: float) -> dict:
    parts = []
    by_sym = {}
    for symbol in SYMBOLS:
        sig = evaluate_rsi_bb(feat_map[symbol], symbol, period, os_, ob_, k)
        taken = apply_position_gate(sig["bias"].to_numpy(), sig.index)
        filled = settle_trades(sig, close_map[symbol], taken)
        filled["strategy"] = "RSI_BB"
        tr = extract_trades(filled)
        tr["strategy"] = "RSI_BB"
        m = _is_metrics(tr)
        by_sym[symbol] = m
        parts.append(tr)
    all_tr = pd.concat(parts, ignore_index=True)
    both = _is_metrics(all_tr)
    return {
        "rsi_period": period,
        "rsi_os": os_,
        "rsi_ob": ob_,
        "bb_k": k,
        "N": both["N"],
        "win_rate": both["win_rate"],
        "EV": both["EV"],
        "N_BTC": by_sym["BTCUSDT"]["N"],
        "wr_BTC": by_sym["BTCUSDT"]["win_rate"],
        "N_ETH": by_sym["ETHUSDT"]["N"],
        "wr_ETH": by_sym["ETHUSDT"]["win_rate"],
    }


def _eligible(df: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    return df.loc[
        df["N"].between(lo, hi)
        & (df["N_BTC"] >= N_SYM_MIN)
        & (df["N_ETH"] >= N_SYM_MIN)
        & df["win_rate"].notna()
    ].copy()


def pick_winner(grid: pd.DataFrame) -> pd.Series:
    """按文件顶部预先写死的规则挑一组。只用 IS 列。"""
    pool = _eligible(grid, N_MOD_LO, N_MOD_HI)
    used = "moderate"
    if pool.empty:
        pool = _eligible(grid, N_RELAX_LO, N_RELAX_HI)
        used = "relaxed"
    if pool.empty:
        raise RuntimeError("网格内没有满足样本数约束的组合")
    pool = pool.copy()
    pool["n_dist"] = (pool["N"] - N_TARGET).abs()
    pool = pool.sort_values(["win_rate", "EV", "n_dist"], ascending=[False, False, True])
    winner = pool.iloc[0].copy()
    winner["select_rule"] = used
    return winner


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("加载特征（仅用于 IS 网格）...", flush=True)
    feat_map = {}
    close_map = {}
    for symbol in SYMBOLS:
        df_1m = load_symbol_1m(symbol, START, END)
        feat = build_feature_frame(df_1m)
        # 入场截止 IS_END；多留 1h 供结算
        cut = pd.Timestamp(IS_END) + pd.Timedelta(hours=1)
        feat_map[symbol] = feat.loc[feat.index < cut].copy()
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
                wr_s = f"{100*wr:.2f}%" if wr is not None else "n/a"
                print(
                    f"[{i}/{n_combo}] RSI{period} {os_}/{ob_} k={k}  "
                    f"N={rec['N']} wr={wr_s} EV={rec['EV']}",
                    flush=True,
                )

    grid = pd.DataFrame(rows)
    grid.to_csv(OUT_DIR / "rsi_bb_search.csv", index=False)
    winner = pick_winner(grid)
    win_d = {
        "rsi_period": int(winner["rsi_period"]),
        "rsi_os": float(winner["rsi_os"]),
        "rsi_ob": float(winner["rsi_ob"]),
        "bb_k": float(winner["bb_k"]),
        "select_rule": str(winner["select_rule"]),
        "IS_N": int(winner["N"]),
        "IS_win_rate": float(winner["win_rate"]),
        "IS_EV": float(winner["EV"]),
        "IS_N_BTC": int(winner["N_BTC"]),
        "IS_N_ETH": int(winner["N_ETH"]),
        "note": "仅用 IS 挑选；OOS 不得用于改阈值",
    }
    (OUT_DIR / "rsi_bb_winner.json").write_text(json.dumps(win_d, indent=2), encoding="utf-8")

    top = _eligible(grid, N_MOD_LO, N_MOD_HI)
    if top.empty:
        top = _eligible(grid, N_RELAX_LO, N_RELAX_HI)
    top = top.sort_values(["win_rate", "EV"], ascending=False).head(15)

    print("\n===== IS 适中样本 Top15（按胜率）=====", flush=True)
    print(
        top[
            ["rsi_period", "rsi_os", "rsi_ob", "bb_k", "N", "win_rate", "EV", "N_BTC", "N_ETH"]
        ].to_string(index=False),
        flush=True,
    )
    print("\n===== 冻结胜者（只看 IS）=====", flush=True)
    print(json.dumps(win_d, indent=2, ensure_ascii=False), flush=True)
    print(f"网格已写入 {OUT_DIR / 'rsi_bb_search.csv'}", flush=True)


if __name__ == "__main__":
    main()
