from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .config import Config
from .models import Signal
from .stats import format_ts

TG_FILE = Path(__file__).resolve().parent.parent / "data" / "telegram.json"


def load_telegram(cfg: Config) -> tuple[str, str]:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.telegram_token or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or cfg.telegram_chat_id or "").strip()
    if (not token or not chat) and TG_FILE.exists():
        data = json.loads(TG_FILE.read_text(encoding="utf-8"))
        token = token or str(data.get("bot_token") or data.get("token") or "").strip()
        chat = chat or str(data.get("chat_id") or data.get("chat") or "").strip()
    return token, chat


def send_telegram(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        raise RuntimeError("Telegram 未配置：请设置 TELEGRAM_BOT_TOKEN 与 TELEGRAM_CHAT_ID，或写 data/telegram.json")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram 发送失败: {body}")


def format_signal_tg(sig: Signal, cfg: Config) -> str:
    t = format_ts(sig.signal_time, cfg.display_tz)
    settle = format_ts(sig.settle_time, cfg.display_tz)
    arrow = "多" if sig.side == "LONG" else "空"
    return (
        f"<b>Range MR V1.1 信号</b>\n"
        f"标的: <b>{sig.symbol}</b>\n"
        f"方向: <b>{arrow} {sig.side}</b>\n"
        f"逻辑: {sig.logic}  ({sig.regime})\n"
        f"开仓价: {sig.open_px:.2f}\n"
        f"触发位: {sig.trigger_level:.2f}\n"
        f"时间: {t}\n"
        f"结算: {settle}（+30m）\n"
        f"支付率: {cfg.payout_rate:.0%}  赢+{cfg.payout_rate:g} / 输-1\n"
        f"{sig.reason}"
    )


def format_settle_tg(sig: Signal, cfg: Config) -> str:
    t = format_ts(sig.settle_time, cfg.display_tz)
    arrow = "多" if sig.side == "LONG" else "空"
    pnl = sig.payout_pnl if sig.payout_pnl is not None else 0.0
    return (
        f"<b>结算 {sig.result}</b> {sig.symbol} {arrow}\n"
        f"逻辑: {sig.logic}\n"
        f"开 {sig.open_px:.2f} → 结 {sig.settle_px:.2f}\n"
        f"支付盈亏: {pnl:+.2f}（本金{cfg.stake:g} / 支付率{cfg.payout_rate:.0%}）\n"
        f"时间: {t}"
    )
