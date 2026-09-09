"""
事件合约跟单面板下单客户端。
面板：http://194.233.90.109:3000
登录后 POST /api/orders/place，与网页「看涨/看跌」同一接口。
"""
from __future__ import annotations

import os
from typing import Any

import requests

from tg import load_dotenv

DEFAULT_URL = "http://194.233.90.109:3000"
DEFAULT_AMOUNT = 50
TIME_INCREMENTS = "THIRTY_MINUTE"
PAYOUT_RATIO = "0.85"


def copybot_enabled() -> bool:
    load_dotenv()
    flag = (os.environ.get("COPYBOT_ENABLED") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(copybot_username() and copybot_password())


def copybot_username() -> str:
    load_dotenv()
    return (os.environ.get("COPYBOT_USERNAME") or "").strip()


def copybot_password() -> str:
    load_dotenv()
    return (os.environ.get("COPYBOT_PASSWORD") or "").strip()


def copybot_url() -> str:
    load_dotenv()
    return (os.environ.get("COPYBOT_URL") or DEFAULT_URL).rstrip("/")


def copybot_amount() -> int:
    load_dotenv()
    raw = (os.environ.get("COPYBOT_ORDER_AMOUNT") or str(DEFAULT_AMOUNT)).strip()
    return int(float(raw))


def copybot_leader_id() -> int | None:
    load_dotenv()
    raw = (os.environ.get("COPYBOT_LEADER_ID") or "").strip()
    if not raw:
        return None
    return int(raw)


def direction_from_bias(bias: int) -> str:
    if int(bias) > 0:
        return "LONG"
    if int(bias) < 0:
        return "SHORT"
    raise ValueError("FLAT 不下单")


def place_payload(
    symbol: str,
    bias: int,
    *,
    leader_id: int,
    amount: int = DEFAULT_AMOUNT,
) -> dict[str, Any]:
    return {
        "leaderAccountId": int(leader_id),
        "symbolName": str(symbol).upper(),
        "direction": direction_from_bias(bias),
        "timeIncrements": TIME_INCREMENTS,
        "orderAmount": str(int(amount)),
        "payoutRatio": PAYOUT_RATIO,
    }


def pick_leader(accounts: list[dict[str, Any]], leader_id: int | None = None) -> dict[str, Any]:
    if not accounts:
        raise RuntimeError("账户列表为空，无法下单")
    if leader_id is not None:
        for acc in accounts:
            if int(acc.get("id")) == int(leader_id):
                return acc
        raise RuntimeError(f"找不到带单账户 id={leader_id}")
    leaders = [a for a in accounts if str(a.get("role") or a.get("type") or "").upper() == "LEADER"]
    pool = leaders or list(accounts)
    valid = [a for a in pool if str(a.get("authStatus") or "").upper() == "VALID"]
    chosen = (valid or pool)[0]
    if str(chosen.get("authStatus") or "").upper() == "EXPIRED":
        raise RuntimeError(f"带单凭证已失效：{chosen.get('name') or chosen.get('id')}")
    return chosen


class CopyBot:
    def __init__(self, base_url: str | None = None, timeout: int = 30) -> None:
        self.base_url = (base_url or copybot_url()).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._user: dict[str, Any] | None = None
        self._leader: dict[str, Any] | None = None

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _decode(self, resp: requests.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if resp.status_code == 401:
            raise RuntimeError(data.get("message") or "跟单面板未登录")
        if not resp.ok or data.get("success") is False:
            raise RuntimeError(data.get("message") or f"跟单面板请求失败 ({resp.status_code})")
        return data if isinstance(data, dict) else {"data": data}

    def login(self, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        user = username if username is not None else copybot_username()
        pwd = password if password is not None else copybot_password()
        if not user or not pwd:
            raise RuntimeError("缺少 COPYBOT_USERNAME / COPYBOT_PASSWORD")
        resp = self.session.post(
            self._url("/api/auth/login"),
            json={"username": user, "password": pwd},
            timeout=self.timeout,
        )
        data = self._decode(resp)
        self._user = data.get("user") or data.get("data") or data
        return self._user

    def accounts(self) -> list[dict[str, Any]]:
        data = self._decode(self.session.get(self._url("/api/accounts"), timeout=self.timeout))
        rows = data.get("data")
        if rows is None:
            rows = data.get("accounts") or []
        return list(rows)

    def ensure_ready(self) -> dict[str, Any]:
        if self._user is None:
            self.login()
        if self._leader is None:
            self._leader = pick_leader(self.accounts(), copybot_leader_id())
        return self._leader

    def place(self, symbol: str, bias: int, *, amount: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        amt = copybot_amount() if amount is None else int(amount)
        leader = self.ensure_ready()
        body = place_payload(symbol, bias, leader_id=int(leader["id"]), amount=amt)
        if dry_run:
            return {"ok": True, "dry_run": True, "payload": body, "leader": leader.get("name") or leader.get("id")}
        resp = self.session.post(self._url("/api/orders/place"), json=body, timeout=self.timeout)
        if resp.status_code == 401:
            self._user = None
            self.login()
            resp = self.session.post(self._url("/api/orders/place"), json=body, timeout=self.timeout)
        data = self._decode(resp)
        return {
            "ok": True,
            "dry_run": False,
            "payload": body,
            "leader": leader.get("name") or leader.get("id"),
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
        return {"ok": False, "skipped": True, "reason": "未配置 COPYBOT_USERNAME/PASSWORD 或已关闭"}
    if rec.get("void"):
        return {"ok": False, "skipped": True, "reason": "作废信号"}
    try:
        out = get_client().place(str(rec["symbol"]), int(rec["bias"]), dry_run=dry_run)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
