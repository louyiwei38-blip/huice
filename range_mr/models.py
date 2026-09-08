from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Bar:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class HvnZone:
    low: float
    high: float
    center: float
    side: str  # upper | lower


@dataclass
class Structure:
    asof_open_time: int
    range_high: float
    range_low: float
    range_mid: float
    range_height: float
    upper_band_low: float
    lower_band_high: float
    atr: float
    is_ranging: bool
    vol_near: float
    vol_typical: float
    poc: float
    vah: float
    val: float
    hvn_zones: list[HvnZone]
    swing_highs: list[float]
    swing_lows: list[float]
    touch_tol: float
    er: float = 1.0


@dataclass
class FlipState:
    direction: str  # up | down
    levels: list[float]
    breakout_level: float
    old_range_high: float
    old_range_low: float
    old_height: float
    atr: float
    confirm_ts: int
    expire_ts: int


@dataclass
class Signal:
    signal_time: int
    side: str  # LONG | SHORT
    logic: str  # BOX_EDGE | HVN | SWING | SR_FLIP
    open_px: float
    regime: str
    reason: str
    range_high: float
    range_low: float
    poc: float
    vah: float
    val: float
    trigger_level: float
    settle_time: int = 0
    settle_px: Optional[float] = None
    pnl_abs: Optional[float] = None
    pnl_pct: Optional[float] = None
    result: Optional[str] = None  # 胜 | 负 | 平 | 缺失
    atr: float = 0.0
    bar_open: float = 0.0
    bar_close: float = 0.0
    er: float = 1.0
    symbol: str = ""
    payout_pnl: Optional[float] = None


@dataclass
class Conflict:
    signal_time: int
    sides: list[str]
    logics: list[str]
    price: float
    reason: str = "同一秒多空同时触发"


@dataclass
class DetectResult:
    signals: list[Signal] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
