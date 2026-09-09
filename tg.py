"""Telegram Bot API。token / chat_id 只从环境或项目根 .env 读，不写进代码。"""
from __future__ import annotations

import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
API = "https://api.telegram.org/bot{token}/sendMessage"


def load_dotenv(path: Path | None = None) -> None:
    p = path or (ROOT / ".env")
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def tg_config() -> tuple[str, str]:
    load_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID。复制 .env.example 为 .env 后填入。"
        )
    return token, chat_id


def html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_telegram(text: str, *, dry_run: bool = False, timeout: int = 30) -> None:
    if dry_run:
        print("----- TG dry-run -----", flush=True)
        print(text, flush=True)
        print("----- /TG -----", flush=True)
        return
    token, chat_id = tg_config()
    resp = requests.post(
        API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"Telegram 发送失败 {resp.status_code}: {resp.text[:400]}")
