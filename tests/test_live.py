"""实盘账本、推送去重、盈亏统计。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from download import last_closed_1m_open
from live import (
    book_stats,
    empty_ledger,
    format_entry,
    format_settle,
    ingest_trades,
    row_to_rec,
    run_once,
    seconds_to_next_5m,
    trade_id,
)


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _trade(
    *,
    symbol="BTCUSDT",
    timestamp="2026-09-09 12:00:00",
    bias=1,
    entry=100.0,
    settle=101.0,
    pnl=0.85,
    void=False,
    settle_time="2026-09-09 12:30:00",
    rsi=18.0,
) -> pd.Series:
    return pd.Series(
        {
            "symbol": symbol,
            "timestamp": _ts(timestamp),
            "bias": bias,
            "entry": entry,
            "settle": settle,
            "settle_time": _ts(settle_time) if settle_time else pd.NaT,
            "pnl": pnl,
            "void": void,
            "rsi": rsi,
            "bb_lower_k": 99.0,
            "bb_upper_k": 110.0,
        }
    )


def test_last_closed_1m_drops_forming_bar():
    now = datetime(2026, 9, 9, 12, 5, 3, tzinfo=timezone.utc)
    assert last_closed_1m_open(now) == datetime(2026, 9, 9, 12, 4, 0, tzinfo=timezone.utc)


def test_trade_id_stable():
    rec = row_to_rec(_trade())
    assert trade_id(rec["symbol"], rec["timestamp"]) == "BTCUSDT|2026-09-09T12:00:00+00:00"


def test_ingest_skips_old_history_but_keeps_fresh_entry():
    ledger = empty_ledger()
    now = datetime(2026, 9, 9, 12, 5, 10, tzinfo=timezone.utc)
    old = _trade(timestamp="2026-09-08 12:00:00", settle_time="2026-09-08 12:30:00")
    fresh = _trade(
        symbol="ETHUSDT",
        timestamp="2026-09-09 12:00:00",
        settle_time="2026-09-09 12:30:00",
        pnl=None,
        settle=None,
    )
    df = pd.DataFrame([old, fresh])
    events = ingest_trades(ledger, df, now)
    kinds = [k for k, _ in events]
    assert kinds == ["entry"]
    assert events[0][1]["symbol"] == "ETHUSDT"
    old_id = trade_id("BTCUSDT", "2026-09-08T12:00:00+00:00")
    assert ledger["trades"][old_id]["notified_entry"] is True
    assert ledger["trades"][old_id]["notified_settle"] is True
    assert ledger["trades"][old_id]["seed"] is True
    eth_id = trade_id("ETHUSDT", "2026-09-09T12:00:00+00:00")
    assert ledger["trades"][eth_id]["seed"] is False


def test_ingest_settle_after_open():
    ledger = empty_ledger()
    now = datetime(2026, 9, 9, 12, 5, 10, tzinfo=timezone.utc)
    open_row = _trade(pnl=None, settle=None, settle_time="2026-09-09 12:30:00")
    ingest_trades(ledger, pd.DataFrame([open_row]), now)
    later = datetime(2026, 9, 9, 12, 30, 8, tzinfo=timezone.utc)
    done = _trade()
    events = ingest_trades(ledger, pd.DataFrame([done]), later)
    assert [k for k, _ in events] == ["settle"]
    assert events[0][1]["pnl"] == 0.85


def test_book_stats_today_and_open():
    ledger = empty_ledger()
    now = datetime(2026, 9, 9, 18, 0, 0, tzinfo=timezone.utc)
    ingest_trades(
        ledger,
        pd.DataFrame(
            [
                _trade(),
                _trade(
                    symbol="ETHUSDT",
                    timestamp="2026-09-09 13:00:00",
                    settle_time="2026-09-09 13:30:00",
                    pnl=-1.0,
                    bias=-1,
                ),
                _trade(
                    symbol="BTCUSDT",
                    timestamp="2026-09-09 17:30:00",
                    settle_time="2026-09-09 18:00:00",
                    pnl=None,
                    settle=None,
                ),
            ]
        ),
        now,
        entry_max_age=0,
        settle_max_age=0,
    )
    for rec in ledger["trades"].values():
        if rec.get("pnl") is not None:
            rec["seed"] = False
    st = book_stats(ledger, now)
    assert st["all"]["N"] == 2
    assert st["all"]["win_rate"] == 0.5
    assert st["all"]["pnl_sum"] == pytest.approx(-0.15)
    assert st["today"]["N"] == 2
    assert len(st["open"]) == 1
    assert "ETHUSDT" in format_settle(ledger["trades"][trade_id("ETHUSDT", "2026-09-09T13:00:00+00:00")], st)


def test_format_entry_has_rule():
    rec = row_to_rec(_trade(pnl=None, settle=None))
    msg = format_entry(rec)
    assert "LONG" in msg
    assert "BTCUSDT" in msg
    assert "20/80" in msg


def test_run_once_dry_run_writes_ledger(tmp_path: Path, monkeypatch):
    import live as live_mod

    monkeypatch.setattr(live_mod, "LEDGER_PATH", tmp_path / "live_ledger.json")
    monkeypatch.setattr(live_mod, "PNL_CSV", tmp_path / "live_pnl.csv")
    monkeypatch.setattr(live_mod, "OUT_DIR", tmp_path)
    now = datetime(2026, 9, 9, 12, 0, 20, tzinfo=timezone.utc)
    df = pd.DataFrame([_trade(pnl=None, settle=None, timestamp="2026-09-09 12:00:00")])
    msgs = run_once(dry_run=True, now=now, trades=df, save=True)
    assert any("开仓" in m for m in msgs)
    saved = (tmp_path / "live_ledger.json").read_text(encoding="utf-8")
    assert "notified_entry" in saved
    assert '"notified_entry": true' in saved


def test_run_once_places_on_fresh_entry(tmp_path: Path, monkeypatch):
    import live as live_mod

    monkeypatch.setattr(live_mod, "LEDGER_PATH", tmp_path / "live_ledger.json")
    monkeypatch.setattr(live_mod, "PNL_CSV", tmp_path / "live_pnl.csv")
    monkeypatch.setattr(live_mod, "OUT_DIR", tmp_path)
    seen = {}

    def fake_place(rec, *, dry_run=False):
        seen["symbol"] = rec["symbol"]
        seen["bias"] = rec["bias"]
        seen["dry_run"] = dry_run
        return {
            "ok": True,
            "dry_run": dry_run,
            "payload": {"symbolName": rec["symbol"], "direction": "LONG", "orderAmount": "50"},
        }

    monkeypatch.setattr(live_mod, "place_for_signal", fake_place)
    now = datetime(2026, 9, 9, 12, 0, 20, tzinfo=timezone.utc)
    df = pd.DataFrame([_trade(pnl=None, settle=None, timestamp="2026-09-09 12:00:00")])
    run_once(dry_run=True, now=now, trades=df, save=True)
    assert seen["symbol"] == "BTCUSDT"
    assert seen["bias"] == 1
    assert seen["dry_run"] is True


def test_format_entry_includes_fill():
    rec = row_to_rec(_trade(pnl=None, settle=None))
    rec["copybot"] = {
        "ok": True,
        "payload": {"symbolName": "BTCUSDT", "direction": "LONG", "orderAmount": "50"},
    }
    assert "已下单 BTCUSDT LONG 50U" in format_entry(rec)


def test_seconds_to_next_5m_positive():
    now = datetime(2026, 9, 9, 12, 1, 0, tzinfo=timezone.utc)
    wait = seconds_to_next_5m(now, lag_sec=8)
    assert 4 * 60 + 7 <= wait <= 4 * 60 + 9


def test_log_prefix_has_utc_timestamp(capsys):
    from live import log

    log("[live] ping")
    out = capsys.readouterr().out
    assert " UTC [live] ping" in out
    assert out[:4].isdigit()
