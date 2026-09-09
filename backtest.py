"""
RFA-30 可复现回放入口。样本内/外一次跑完，不在 OOS 上改规则。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from download import END, START, SYMBOLS, load_symbol_1m
from indicators import build_feature_frame, to_close_index
from report import (
    OOS_END,
    build_metrics,
    pick_audit_trades,
    save_outputs,
    write_markdown,
)
from rsi_bb import scan_rsi_bb
from strategy import (
    apply_position_gate,
    evaluate_base_1h,
    evaluate_base_long,
    evaluate_base_mom,
    evaluate_rfa30,
    extract_trades,
    settle_trades,
)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"

STRATEGIES = {
    "RFA30": evaluate_rfa30,
    "RSI_BB": None,  # 走 scan_rsi_bb，与 detect.py 同一实现
    "BASE_LONG": evaluate_base_long,
    "BASE_MOM": evaluate_base_mom,
    "BASE_1H": evaluate_base_1h,
}


def run_symbol(symbol: str, df_1m: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    print(f"[feat] {symbol} 1m={len(df_1m)}", flush=True)
    feat = build_feature_frame(df_1m)
    close_1m = to_close_index(df_1m)["close"]
    trade_frames = []
    signal_frames = []
    equity_frames = []
    for name, fn in STRATEGIES.items():
        print(f"[sig] {symbol} {name}", flush=True)
        if name == "RSI_BB":
            trades, filled = scan_rsi_bb(df_1m, symbol, feat=feat, close_1m=close_1m)
        else:
            sig = fn(feat, symbol)
            taken = apply_position_gate(sig["bias"].to_numpy(), sig.index)
            filled = settle_trades(sig, close_1m, taken)
            filled["strategy"] = name
            trades = extract_trades(filled)
            trades["strategy"] = name
        trade_frames.append(trades)

        cols = [
            "symbol",
            "strategy",
            "regime",
            "setup",
            "bias",
            "quality",
            "close",
            "entry",
            "settle_time",
            "settle",
            "pnl",
            "win",
            "traded",
            "void",
        ]
        snap = filled.reset_index().rename(columns={"close_time": "timestamp", "index": "timestamp"})
        if "timestamp" not in snap.columns:
            snap = snap.rename(columns={snap.columns[0]: "timestamp"})
        keep = ["timestamp"] + [c for c in cols if c in snap.columns]
        signal_frames.append(snap[keep])

        eq = trades.loc[~trades["void"].fillna(False) & trades["pnl"].notna(), ["timestamp", "symbol", "setup", "pnl"]].copy()
        if eq.empty:
            continue
        eq["strategy"] = name
        eq = eq.sort_values("timestamp")
        eq["cum_pnl"] = eq["pnl"].cumsum()
        equity_frames.append(eq)

    trades_all = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    signals_all = pd.concat(signal_frames, ignore_index=True) if signal_frames else pd.DataFrame()
    equity_all = pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame()
    return trades_all, signals_all, equity_all


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"回放区间 {START.isoformat()} → {END.isoformat()}")
    all_trades = []
    all_signals = []
    all_equity = []
    for symbol in SYMBOLS:
        df_1m = load_symbol_1m(symbol, START, END)
        if df_1m.empty:
            raise RuntimeError(f"{symbol} 无数据")
        # 回放只用截止日前的入场；多留的 1m 仅供结算
        trades, signals, equity = run_symbol(symbol, df_1m)
        all_trades.append(trades)
        all_signals.append(signals)
        all_equity.append(equity)

    trades = pd.concat(all_trades, ignore_index=True)
    signals = pd.concat(all_signals, ignore_index=True)
    equity = pd.concat(all_equity, ignore_index=True) if any(len(x) for x in all_equity) else pd.DataFrame()

    # 入场落在样本截止之前
    cutoff = pd.Timestamp(OOS_END)
    if not trades.empty:
        trades["timestamp"] = pd.to_datetime(trades["timestamp"], utc=True)
        trades = trades.loc[trades["timestamp"] < cutoff].copy()
    if not equity.empty:
        equity["timestamp"] = pd.to_datetime(equity["timestamp"], utc=True)
        equity = equity.loc[equity["timestamp"] < cutoff].copy()

    metrics = build_metrics(trades)
    audit = pick_audit_trades(trades.loc[trades["strategy"] == "RSI_BB"])
    md = write_markdown(metrics, audit, trades)
    save_outputs(OUT_DIR, trades, metrics, equity, md, signals)
    print(md)
    print(f"\n产出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
