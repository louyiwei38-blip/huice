"""
RSI_BB 扫描：回放与信号检测共用，避免两套逻辑漂移。
冻结参数：RSI(7) 20/80 + 布林 k=2.2；事件合约无资金费，不跳过资金费窗口。
默认跳过北京 20:00–23:00（UTC 12:00–15:00）；对齐冻结 IS 时传 skip_bj_session=False。
"""
from __future__ import annotations

import pandas as pd

from indicators import build_feature_frame, to_close_index
from strategy import apply_position_gate, evaluate_rsi_bb, extract_trades, settle_trades

STRATEGY_NAME = "RSI_BB"


def scan_rsi_bb(
    df_1m: pd.DataFrame,
    symbol: str,
    feat: pd.DataFrame | None = None,
    close_1m: pd.Series | None = None,
    ignore_funding: bool = True,
    skip_bj_session: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    与 backtest 中 RSI_BB 路径相同：特征 → 规则 → 持仓门控 → T+30 结算。
    返回 (trades, filled_5m)。
    """
    if feat is None:
        feat = build_feature_frame(df_1m)
    if close_1m is None:
        close_1m = to_close_index(df_1m)["close"]
    sig = evaluate_rsi_bb(
        feat,
        symbol,
        ignore_funding=ignore_funding,
        skip_bj_session=skip_bj_session,
    )
    taken = apply_position_gate(sig["bias"].to_numpy(), sig.index)
    filled = settle_trades(sig, close_1m, taken)
    filled["strategy"] = STRATEGY_NAME
    trades = extract_trades(filled)
    trades["strategy"] = STRATEGY_NAME
    return trades, filled


def valid_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    return trades.loc[~trades["void"].fillna(False) & trades["pnl"].notna()].copy()
