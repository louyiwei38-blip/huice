from __future__ import annotations

from dataclasses import dataclass


INTERVAL_30M_MS = 30 * 60 * 1000
INTERVAL_1M_MS = 60 * 1000
SETTLE_MS = 30 * 60 * 1000


@dataclass
class Config:
    symbol: str = "ETHUSDT"
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    market: str = "binance_usdm"

    lookback: int = 48
    volume_near: int = 6
    volume_ratio: float = 0.75

    edge_frac: float = 0.20
    drop_extreme_bars: int = 1

    vp_buckets: int = 50
    value_area_pct: float = 0.70
    hvn_percentile: float = 0.70

    swing_left: int = 3
    swing_keep: int = 2
    swing_min_mid_frac: float = 0.30

    atr_period: int = 14
    touch_pct: float = 0.0008
    touch_atr_mult: float = 0.15
    breakout_atr_mult: float = 0.15

    flip_bars: int = 8
    flip_travel_mult: float = 1.0

    warmup_30m: int = 80

    require_cross: bool = True
    require_ranging: bool = False  # BOX_EDGE/SWING 不要求 is_ranging

    # quality filters (V1.1)
    enabled_logics: tuple[str, ...] = ("BOX_EDGE", "SWING", "SR_FLIP")
    allowed_sides: tuple[str, ...] = ("LONG", "SHORT")
    utc_weekdays: tuple[int, ...] | None = None  # 0=Mon ... 5=Sat 6=Sun, None=all
    utc_hours: tuple[int, ...] | None = None  # 0-23 UTC, None=all
    min_confluence: int = 1
    merge_same_ts: bool = False
    cooldown_bars: int = 2  # BOX_EDGE / SWING 同向冷却（30m 根数）
    cooldown_bars_sr_flip: int | None = 4  # SR_FLIP 单独更长；None 则跟 cooldown_bars
    require_with_bar: bool = True
    skip_chase_atr: float = 0.25
    max_er: float | None = 0.35  # drop RANGE signals with Kaufman ER above this

    live_poll_sec: float = 3.0
    payout_rate: float = 0.85  # 赢：+支付率 * 本金；输：-本金
    stake: float = 250.0
    binance_base: str = "https://fapi.binance.com"
    display_tz: str = "Asia/Shanghai"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    trade_base_url: str = "http://194.233.90.109:3000"
    trade_amount: int = 100
    trade_period: str = "THIRTY_MINUTE"
