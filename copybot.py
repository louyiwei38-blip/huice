"""
事件合约跟单：向 webhook 推 copy-signal。
面板：http://8.210.132.210:3000
POST /api/webhook/copy-signal  Authorization: Bearer <token>
"""
from __future__ import annotations

import os
from typing import Any

import requests

from tg import load_dotenv

DEFAULT_URL = "http://8.210.132.210:3000"
DEFAULT_AMOUNT = 50
WEBHOOK_PATH = "/api/webhook/copy-signal"
TIME_INCREMENTS = "THIRTY_MINUTE"


def copybot_enabled() -> bool:
    load_dotenv()
    flag = (os.environ.get("COPYBOT_ENABLED") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(copybot_token())


def copybot_token() -> str:
    load_dotenv()
    return (os.environ.get("COPYBOT_WEBHOOK_TOKEN") or "").strip()


def copybot_url() -> str:
    load_dotenv()
    return (os.environ.get("COPYBOT_URL") or DEFAULT_URL).rstrip("/")


def copybot_amount() -> int:
    load_dotenv()
    raw = (os.environ.get("COPYBOT_ORDER_AMOUNT") or str(DEFAULT_AMOUNT)).strip()
    return int(float(raw))


def direction_from_bias(bias: int) -> str:
    if int(bias) > 0:
        return "LONG"
    if int(bias) < 0:
        return "SHORT"
    raise ValueError("FLAT 不下单")


def place_payload(symbol: str, bias: int, *, amount: int = DEFAULT_AMOUNT) -> dict[str, Any]:
    return {
        "symbolName": str(symbol).upper(),
        "direction": direction_from_bias(bias),
        "timeIncrements": TIME_INCREMENTS,
        "orderAmount": str(int(amount)),
    }


class CopyBot:
    def __init__(self, base_url: str | None = None, timeout: int = 30) -> None:
        self.base_url = (base_url or copybot_url()).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def webhook_url(self) -> str:
        return f"{self.base_url}{WEBHOOK_PATH}"

    def _auth_headers(self) -> dict[str, str]:
        token = copybot_token()
        if not token:
            raise RuntimeError("缺少 COPYBOT_WEBHOOK_TOKEN")
        return {"Authorization": f"Bearer {token}"}

    def _decode(self, resp: requests.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if resp.status_code == 401:
            raise RuntimeError(data.get("message") or "webhook token 无效")
        if not resp.ok or data.get("success") is False:
            raise RuntimeError(data.get("message") or f"跟单 webhook 失败 ({resp.status_code})")
        return data if isinstance(data, dict) else {"data": data}

    def ping(self) -> int:
        resp = self.session.get(self.base_url + "/", timeout=self.timeout)
        return int(resp.status_code)

    def place(self, symbol: str, bias: int, *, amount: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        amt = copybot_amount() if amount is None else int(amount)
        body = place_payload(symbol, bias, amount=amt)
        if dry_run:
            return {"ok": True, "dry_run": True, "payload": body, "url": self.webhook_url()}
        resp = self.session.post(
            self.webhook_url(),
            json=body,
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        data = self._decode(resp)
        return {
            "ok": True,
            "dry_run": False,
            "payload": body,
            "url": self.webhook_url(),
            "response": data.get("data", data),
        }


_client: CopyBot | None = None


def get_client() -> CopyBot:
    global _client
    if _client is None:
        _client = CopyBot()
    return _client


def place_for_signal(rec: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    prev = rec.get("copybot")
    if isinstance(prev, dict) and prev.get("ok") and not prev.get("dry_run"):
        return prev
    if not copybot_enabled():
        return {"ok": False, "skipped": True, "reason": "未配置 COPYBOT_WEBHOOK_TOKEN 或已关闭"}
    if rec.get("void"):
        return {"ok": False, "skipped": True, "reason": "作废信号"}
    try:
        out = get_client().place(str(rec["symbol"]), int(rec["bias"]), dry_run=dry_run)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
