"""Search quality filters on cached 180d klines. Generation grid is small; time/logic filters are post-applied."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone

from range_mr.cache import load_cached
from range_mr.config import Config
from range_mr.replay import replay


def pack(signals) -> dict:
    done = [s for s in signals if s.result in ("胜", "负")]
    n = len(done)
    if n == 0:
        return {"n": 0, "wr": 0.0, "avg": 0.0, "sum": 0.0}
    win = sum(1 for s in done if s.result == "胜")
    sm = sum(s.pnl_pct or 0.0 for s in done)
    return {"n": n, "wr": win / n, "avg": sm / n, "sum": sm}


def score(p: dict) -> float:
    n, wr, avg = p["n"], p["wr"], p["avg"]
    if n < 90 or n > 400:
        return -999.0
    density = 1.0 - abs(n - 200) / 400.0
    return wr * 1000.0 + avg * 100000.0 + density * 5.0


def post_filter(signals, logics=None, weekdays=None, hours=None, sides=None,
                with_bar=False, chase=0.0, max_er=None):
    out = []
    for s in signals:
        utc = datetime.fromtimestamp(s.signal_time / 1000.0, tz=timezone.utc)
        if logics and s.logic not in logics:
            continue
        if weekdays is not None and utc.weekday() not in weekdays:
            continue
        if hours is not None and utc.hour not in hours:
            continue
        if sides and s.side not in sides:
            continue
        if with_bar:
            if s.side == "LONG" and s.bar_close < s.bar_open:
                continue
            if s.side == "SHORT" and s.bar_close > s.bar_open:
                continue
        if chase > 0 and s.atr > 0:
            if s.side == "LONG" and s.bar_close < s.range_low - chase * s.atr:
                continue
            if s.side == "SHORT" and s.bar_close > s.range_high + chase * s.atr:
                continue
        if max_er is not None and s.regime == "RANGE" and s.er > max_er:
            continue
        out.append(s)
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    bars_30, bars_1, _, _ = load_cached()
    start_ms = bars_1[1].open_time
    end_ms = bars_1[-40].open_time
    print(f"cache 30m={len(bars_30)} 1m={len(bars_1)}")

    generators = [
        ("base", Config()),
        ("tight", replace(Config(), volume_ratio=0.60, edge_frac=0.15, cooldown_bars=2)),
        ("chop", replace(Config(), volume_ratio=0.55, edge_frac=0.12, cooldown_bars=2, max_er=0.32)),
        ("cd4", replace(Config(), cooldown_bars=4)),
        ("edge12", replace(Config(), edge_frac=0.12, volume_ratio=0.65, cooldown_bars=2)),
    ]

    generated = []
    for name, cfg in generators:
        print(f"replay {name} ...", flush=True)
        sigs, _, _ = replay(bars_30, bars_1, cfg, start_ms, end_ms)
        generated.append((name, sigs))
        p = pack(sigs)
        print(f"  raw n={p['n']} wr={p['wr']:.2%} avg={p['avg']*10000:.2f}bp")

    logic_opts = [
        ("ALL", None),
        ("noHVN", ("BOX_EDGE", "SWING", "SR_FLIP")),
        ("SW+FL", ("SWING", "SR_FLIP")),
        ("SWING", ("SWING",)),
        ("FLIP", ("SR_FLIP",)),
        ("HVN+SW", ("HVN", "SWING")),
        ("BOX+SW", ("BOX_EDGE", "SWING")),
    ]
    session_opts = [
        ("alld", None, None),
        ("Tue", (1,), None),
        ("Sat", (5,), None),
        ("TueSat", (1, 5), None),
        ("SatPM", (5,), tuple(range(12, 24))),
        ("noFS", (0, 1, 2, 3, 5), None),
        ("NY", None, tuple(range(13, 21))),
        ("asiaOff", None, tuple([h for h in range(24) if h not in (0, 6, 7, 9, 21)])),
    ]
    extra_opts = [
        ("raw", False, 0.0, None, None),
        ("bar", True, 0.25, None, None),
        ("barER", True, 0.25, 0.35, None),
        ("long", True, 0.25, None, ("LONG",)),
        ("satLongLike", True, 0.20, 0.35, None),
    ]

    rows = []
    for gname, sigs in generated:
        for lname, logics in logic_opts:
            for sname, wd, hrs in session_opts:
                for ename, with_bar, chase, mer, sides in extra_opts:
                    flt = post_filter(
                        sigs,
                        logics=logics,
                        weekdays=wd,
                        hours=hrs,
                        sides=sides,
                        with_bar=with_bar,
                        chase=chase,
                        max_er=mer,
                    )
                    p = pack(flt)
                    p["name"] = f"{gname}|{lname}|{sname}|{ename}"
                    p["sc"] = score(p)
                    rows.append(p)

    rows.sort(key=lambda x: x["sc"], reverse=True)
    print("\n===== TOP 20 (n=90..400) =====")
    for p in rows[:20]:
        print(f"wr={p['wr']:6.2%} n={p['n']:4d} avg={p['avg']*10000:6.2f}bp sum={p['sum']*100:6.2f}%  {p['name']}")

    viable = [r for r in rows if r["n"] >= 90]
    viable.sort(key=lambda x: (x["wr"], x["avg"]), reverse=True)
    print("\n===== HIGHEST WIN RATE n>=90 =====")
    for p in viable[:15]:
        print(f"wr={p['wr']:6.2%} n={p['n']:4d} avg={p['avg']*10000:6.2f}bp  {p['name']}")

    mid = [r for r in rows if 120 <= r["n"] <= 280]
    mid.sort(key=lambda x: (x["wr"], x["avg"]), reverse=True)
    print("\n===== HIGHEST WR 120<=n<=280 =====")
    for p in mid[:12]:
        print(f"wr={p['wr']:6.2%} n={p['n']:4d} avg={p['avg']*10000:6.2f}bp  {p['name']}")


if __name__ == "__main__":
    main()
