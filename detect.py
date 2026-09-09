"""
RSI_BB 信号检测。与回放共用 scan_rsi_bb，参数冻结为 RSI(7) 20/80、布林 k=2.2。

用法：
  python detect.py              # 全样本扫描，写出 output/detect_signals.csv
  python detect.py --align      # 与 IS 回放对齐：N=3704、胜率 59.69%
  python detect.py --latest     # 打印每标的最近一根已收盘 5m 是否触发
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from download import END, START, SYMBOLS, load_symbol_1m
from report import IS_END, IS_START, OOS_END
from rsi_bb import scan_rsi_bb, valid_trades
from strategy import (
    HOLD_MINUTES,
    RSI_BB_K,
    RSI_BB_OB,
    RSI_BB_OS,
    RSI_BB_PERIOD,
)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"

# 与 output/REPORT.md、rsi_bb_winner.json 的 IS 回放对齐
ALIGN_IS_N = 3704
ALIGN_IS_WR = 0.5969222462203023
ALIGN_IS_EV = 0.1048461123110151


def _in_is(ts: pd.Series) -> pd.Series:
    t = pd.to_datetime(ts, utc=True)
    return (t >= pd.Timestamp(IS_START)) & (t < pd.Timestamp(IS_END))


def scan_all(cutoff: pd.Timestamp | None = None) -> pd.DataFrame:
    frames = []
    for symbol in SYMBOLS:
        print(f"[detect] {symbol}", flush=True)
        df_1m = load_symbol_1m(symbol, START, END)
        trades, _ = scan_rsi_bb(df_1m, symbol)
        frames.append(trades)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if out.empty:
        return out
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    cap = cutoff if cutoff is not None else pd.Timestamp(OOS_END)
    return out.loc[out["timestamp"] < cap].copy()


def is_stats(trades: pd.DataFrame) -> dict:
    v = valid_trades(trades)
    v = v.loc[_in_is(v["timestamp"])]
    n = int(len(v))
    if n == 0:
        return {"N": 0, "win_rate": None, "EV": None}
    pnl = v["pnl"].to_numpy(dtype=float)
    return {"N": n, "win_rate": float((pnl > 0).mean()), "EV": float(pnl.mean())}


def check_align(trades: pd.DataFrame, replay_csv: Path | None = None) -> list[str]:
    """检测脚本 vs 冻结 IS 回放。不一致则返回错误行。"""
    errs = []
    st = is_stats(trades)
    if st["N"] != ALIGN_IS_N:
        errs.append(f"IS N={st['N']} 期望 {ALIGN_IS_N}")
    if st["win_rate"] is None or abs(st["win_rate"] - ALIGN_IS_WR) > 1e-9:
        errs.append(f"IS 胜率={st['win_rate']} 期望 {ALIGN_IS_WR}")
    if st["EV"] is None or abs(st["EV"] - ALIGN_IS_EV) > 1e-9:
        errs.append(f"IS EV={st['EV']} 期望 {ALIGN_IS_EV}")

    if replay_csv is not None and replay_csv.exists():
        replay = pd.read_csv(
            replay_csv,
            usecols=["timestamp", "symbol", "bias", "strategy", "void", "pnl"],
            low_memory=False,
        )
        replay = replay.loc[replay["strategy"] == "RSI_BB"].copy()
        replay["timestamp"] = pd.to_datetime(replay["timestamp"], utc=True)
        rv = valid_trades(replay)
        rv = rv.loc[_in_is(rv["timestamp"])]
        if int(len(rv)) != ALIGN_IS_N:
            return errs  # trades.csv 仍是旧阈值，只核常数
        det = valid_trades(trades)
        det = det.loc[_in_is(det["timestamp"])]
        keys = ["timestamp", "symbol", "bias"]
        rv["bias"] = rv["bias"].astype(int)
        det["bias"] = det["bias"].astype(int)
        a = set(map(tuple, rv[keys].itertuples(index=False, name=None)))
        b = set(map(tuple, det[keys].itertuples(index=False, name=None)))
        if a != b:
            only_replay = sorted(a - b)[:5]
            only_det = sorted(b - a)[:5]
            errs.append(
                f"与 {replay_csv.name} 的 IS 成交集合不一致 "
                f"replay_only={len(a-b)} detect_only={len(b-a)} "
                f"例 replay={only_replay} detect={only_det}"
            )
    return errs


def print_latest(trades: pd.DataFrame, filled_by_symbol: dict[str, pd.DataFrame] | None = None) -> None:
    print(
        f"规则：15m RSI({RSI_BB_PERIOD}) ≤{RSI_BB_OS} 且收盘≤下轨做多；"
        f"RSI≥{RSI_BB_OB} 且收盘≥上轨做空；布林 k={RSI_BB_K}；持仓 {HOLD_MINUTES}min"
    )
    if filled_by_symbol:
        for symbol, filled in filled_by_symbol.items():
            row = filled.iloc[-1]
            ts = filled.index[-1]
            raw = {1: "LONG", -1: "SHORT"}.get(int(row["bias"]), "FLAT")
            if int(row["bias"]) == 0:
                status = "FLAT"
            elif bool(row.get("traded")):
                status = "ENTER"
            else:
                status = "BLOCKED"
            print(
                f"{symbol} 最近5m={ts} raw={raw} status={status} "
                f"rsi={row.get('rsi')} close={row.get('m15_close')} "
                f"bb_low={row.get('bb_lower_k')} bb_up={row.get('bb_upper_k')}"
            )
        return
    if trades.empty:
        print("无成交。")
        return
    last = trades.sort_values("timestamp").groupby("symbol").tail(1)
    print(last[["timestamp", "symbol", "bias", "entry", "rsi", "bb_lower_k", "bb_upper_k", "pnl"]].to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="RSI_BB 信号检测（与回放对齐）")
    ap.add_argument("--align", action="store_true", help="校验 IS 与冻结回放 N/胜率")
    ap.add_argument("--latest", action="store_true", help="打印每标的最近 5m 状态")
    ap.add_argument("--is-only", action="store_true", help="只输出 IS 区间信号")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"RSI_BB 检测  RSI({RSI_BB_PERIOD}) {RSI_BB_OS}/{RSI_BB_OB}  k={RSI_BB_K}",
        flush=True,
    )

    if args.latest:
        filled_map = {}
        trade_frames = []
        for symbol in SYMBOLS:
            df_1m = load_symbol_1m(symbol, START, END)
            trades, filled = scan_rsi_bb(df_1m, symbol)
            filled_map[symbol] = filled
            trade_frames.append(trades)
        trades = pd.concat(trade_frames, ignore_index=True)
        print_latest(trades, filled_map)
        return

    trades = scan_all()
    trades_out = trades.loc[_in_is(trades["timestamp"])].copy() if args.is_only else trades

    path = OUT_DIR / "detect_signals.csv"
    trades_out.to_csv(path, index=False)
    st = is_stats(trades)
    wr = st["win_rate"]
    wr_s = f"{100.0 * wr:.2f}%" if wr is not None else "n/a"
    print(f"IS 对齐核对：N={st['N']} 胜率={wr_s} EV={st['EV']}", flush=True)
    print(f"写出 {path}  rows={len(trades_out)}", flush=True)

    if args.align:
        errs = check_align(trades, OUT_DIR / "trades.csv")
        if errs:
            print("对齐失败：", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            raise SystemExit(1)
        print(f"对齐通过：与 IS 冻结回放 N={ALIGN_IS_N}、胜率 {100*ALIGN_IS_WR:.2f}% 一致。")


if __name__ == "__main__":
    main()
