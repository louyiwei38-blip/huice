"""Place event-contract orders on the copy-trading panel."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import requests

from .config import Config
from .models import Signal

TRADE_FILE = Path(__file__).resolve().parent.parent / "data" / "trade.json"

PERIOD_30M = "THIRTY_MINUTE"
PAYOUT_30M = "0.85"


def load_trade_settings(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or Config()
    data: dict[str, Any] = {}
    if TRADE_FILE.exists():
        data = json.loads(TRADE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}

    enabled_raw = os.environ.get("TRADE_ENABLED", data.get("enabled", False))
    if isinstance(enabled_raw, str):
        enabled = enabled_raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        enabled = bool(enabled_raw)

    username = (os.environ.get("TRADE_USERNAME") or data.get("username") or "").strip()
    password = (os.environ.get("TRADE_PASSWORD") or data.get("password") or "").strip()
    base_url = (
        os.environ.get("TRADE_BASE_URL")
        or data.get("base_url")
        or cfg.trade_base_url
        or ""
    ).strip().rstrip("/")
    leader_id = os.environ.get("TRADE_LEADER_ACCOUNT_ID") or data.get("leader_account_id")
    try:
        leader_id = int(leader_id) if leader_id not in (None, "") else None
    except (TypeError, ValueError):
        leader_id = None

    amount = os.environ.get("TRADE_ORDER_AMOUNT") or data.get("order_amount") or cfg.trade_amount
    try:
        amount = int(float(amount))
    except (TypeError, ValueError):
        amount = 50

    period = (
        os.environ.get("TRADE_PERIOD")
        or data.get("time_increments")
        or cfg.trade_period
        or PERIOD_30M
    ).strip()
    payout = str(
        os.environ.get("TRADE_PAYOUT_RATIO")
        or data.get("payout_ratio")
        or PAYOUT_30M
    ).strip()

    return {
        "enabled": enabled,
        "base_url": base_url,
        "username": username,
        "password": password,
        "leader_account_id": leader_id,
        "order_amount": amount,
        "time_increments": period,
        "payout_ratio": payout,
    }


class TradeBot:
    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.base_url = settings["base_url"].rstrip("/")
        self.username = settings["username"]
        self.password = settings["password"]
        self.order_amount = int(settings["order_amount"])
        self.time_increments = settings["time_increments"] or PERIOD_30M
        self.payout_ratio = str(settings["payout_ratio"] or PAYOUT_30M)
        self.preferred_leader_id: Optional[int] = settings.get("leader_account_id")
        self.leader_id: Optional[int] = self.preferred_leader_id
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "range-mr-trade/1.0"})
        self._user: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, cfg: Config | None = None) -> Optional["TradeBot"]:
        settings = load_trade_settings(cfg)
        if not settings["enabled"]:
            return None
        if not settings["base_url"] or not settings["username"] or not settings["password"]:
            print("下单已开启但缺少 base_url / username / password，跳过自动下单")
            return None
        return cls(settings)

    def connect(self) -> dict[str, Any]:
        self._login()
        leader = self._resolve_leader()
        self.leader_id = int(leader["id"])
        return {
            "user": self._user,
            "leader_id": self.leader_id,
            "leader_name": leader.get("name") or leader.get("loginUsername") or "",
            "order_amount": self.order_amount,
            "period": self.time_increments,
        }

    def place(self, sig: Signal) -> dict[str, Any]:
        if self.leader_id is None:
            self.connect()
        body = {
            "leaderAccountId": self.leader_id,
            "symbolName": sig.symbol,
            "direction": sig.side,
            "timeIncrements": self.time_increments,
            "orderAmount": self.order_amount,
            "payoutRatio": self.payout_ratio,
        }
        data = self._request("POST", "/api/orders/place", json_body=body)
        return data.get("data") if isinstance(data.get("data"), dict) else data

    def _login(self) -> None:
        data = self._request(
            "POST",
            "/api/auth/login",
            json_body={"username": self.username, "password": self.password},
            auth=False,
        )
        user = data.get("user") or (data.get("data") or {}).get("user")
        if not user:
            raise RuntimeError(f"登录成功但未返回用户: {data}")
        self._user = user

    def _resolve_leader(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/accounts")
        accounts = payload.get("data") or []
        if not isinstance(accounts, list):
            raise RuntimeError(f"账户列表格式异常: {payload}")
        if self.preferred_leader_id is not None:
            for acc in accounts:
                if int(acc.get("id") or 0) == self.preferred_leader_id:
                    return acc
            raise RuntimeError(f"找不到带单账户 id={self.preferred_leader_id}")
        leaders = [
            acc
            for acc in accounts
            if str(acc.get("role") or "").upper() == "LEADER" and acc.get("enabled", True)
        ]
        if not leaders:
            leaders = [acc for acc in accounts if str(acc.get("role") or "").upper() == "LEADER"]
        if not leaders:
            raise RuntimeError("账户列表里没有带单用户 LEADER，请先在面板添加")
        return leaders[0]

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
        _retry: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(
                method,
                url,
                json=json_body,
                headers={"content-type": "application/json"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"请求下单面板失败 {method} {path}: {exc}") from exc

        if resp.status_code == 401 and auth and _retry:
            self._login()
            return self._request(method, path, json_body=json_body, auth=auth, _retry=False)

        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"下单面板返回非 JSON ({resp.status_code}): {resp.text[:300]}") from exc

        if resp.status_code == 401 or data.get("success") is False:
            raise RuntimeError(data.get("message") or f"下单面板错误 ({resp.status_code})")
        if not resp.ok:
            raise RuntimeError(data.get("message") or f"下单面板 HTTP {resp.status_code}")
        return data if isinstance(data, dict) else {"data": data}
