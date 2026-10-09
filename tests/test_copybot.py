"""跟单 webhook payload。"""
from __future__ import annotations

import pytest

from copybot import direction_from_bias, place_payload


def test_direction_from_bias():
    assert direction_from_bias(1) == "LONG"
    assert direction_from_bias(-1) == "SHORT"
    with pytest.raises(ValueError):
        direction_from_bias(0)


def test_place_payload_matches_webhook():
    body = place_payload("btcusdt", 1, amount=5)
    assert body == {
        "symbolName": "BTCUSDT",
        "direction": "LONG",
        "timeIncrements": "THIRTY_MINUTE",
        "orderAmount": "5",
    }
    short = place_payload("ETHUSDT", -1, amount=100)
    assert short["direction"] == "SHORT"
    assert short["symbolName"] == "ETHUSDT"
    assert short["orderAmount"] == "100"
