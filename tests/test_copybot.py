"""跟单面板下单 payload / 选带单账户。"""
from __future__ import annotations

import pytest

from copybot import direction_from_bias, pick_leader, place_payload


def test_direction_from_bias():
    assert direction_from_bias(1) == "LONG"
    assert direction_from_bias(-1) == "SHORT"
    with pytest.raises(ValueError):
        direction_from_bias(0)


def test_place_payload_default_50():
    body = place_payload("btcusdt", 1, leader_id=3, amount=50)
    assert body == {
        "leaderAccountId": 3,
        "symbolName": "BTCUSDT",
        "direction": "LONG",
        "timeIncrements": "THIRTY_MINUTE",
        "orderAmount": "50",
        "payoutRatio": "0.85",
    }
    short = place_payload("ETHUSDT", -1, leader_id=3, amount=50)
    assert short["direction"] == "SHORT"
    assert short["symbolName"] == "ETHUSDT"


def test_pick_leader_prefers_valid():
    accounts = [
        {"id": 1, "role": "FOLLOWER", "authStatus": "VALID", "name": "f"},
        {"id": 2, "role": "LEADER", "authStatus": "EXPIRED", "name": "old"},
        {"id": 3, "role": "LEADER", "authStatus": "VALID", "name": "ok"},
    ]
    assert pick_leader(accounts)["id"] == 3
    assert pick_leader(accounts, leader_id=2)["id"] == 2
