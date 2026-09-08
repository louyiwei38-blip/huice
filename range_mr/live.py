from __future__ import annotations

from dataclasses import dataclass, replace

from .binance_data import BinanceUMFutures
from .config import Config, INTERVAL_30M_MS, SETTLE_MS
from .detector import Detector
from .models import Signal
from .replay import apply_binary_payout
from .stats import format_ts, print_conflict, print_signal
from .telegram_notify import format_settle_tg, format_signal_tg, load_telegram, send_telegram


@dataclass
class SymbolBook:
    symbol: str
    cfg: Config
    det: Detector
    last_30m_open: int
    pending: list[Signal]
    seen: set[tuple[int, str, str, str]]


def run_live(cfg: Config) -> None:
    symbols = cfg.symbols or (cfg.symbol,)
    client = BinanceUMFutures(cfg.binance_base)
    books: list[SymbolBook] = []
    for symbol in symbols:
        scfg = replace(cfg, symbol=symbol)
        det = Detector(scfg)
        history = client.fetch_closed_klines(symbol, "30m", limit=max(200, cfg.warmup_30m))
        if len(history) < cfg.warmup_30m:
            raise RuntimeError(f"{symbol} 30m 历史不足: {len(history)}")
        for bar in history:
            det.on_30m_close(bar)
        st = det.structure
        print(
            f"LIVE {symbol} 已加载 {len(history)} 根30m | "
            f"盘整={st.is_ranging if st else None} | "
            f"箱体={st.range_low if st else 0:.1f}-{st.range_high if st else 0:.1f}"
        )
        books.append(
            SymbolBook(
                symbol=symbol,
                cfg=scfg,
                det=det,
                last_30m_open=history[-1].open_time,
                pending=[],
                seen=set(),
            )
        )

    token, chat = load_telegram(cfg)
    if token and chat:
        print(f"Telegram 已连接 chat_id={chat}")
        try:
            send_telegram(
                token,
                chat,
                "Range MR V1.1 已启动\n标的: " + ", ".join(symbols) + f"\n支付率 {cfg.payout_rate:.0%}",
            )
        except Exception as exc:
            print(f"Telegram 启动消息失败: {exc}")
    else:
        print("未配置 Telegram：信号只打印在终端。请设置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 或 data/telegram.json")

    import time

    while True:
        try:
            for book in books:
                _poll(client, book, token, chat)
        except KeyboardInterrupt:
            print("停止 live")
            return
        except Exception as exc:
            print(f"live 轮询异常: {exc}")
        time.sleep(cfg.live_poll_sec)


def _poll(client: BinanceUMFutures, book: SymbolBook, token: str, chat: str) -> None:
    cfg = book.cfg
    det = book.det
    bars = client.fetch_closed_klines(book.symbol, "30m", limit=5)
    for bar in bars:
        if bar.open_time > book.last_30m_open:
            det.on_30m_close(bar)
            book.last_30m_open = bar.open_time
            st = det.structure
            if st:
                print(
                    f"{book.symbol} 30m收盘 {format_ts(bar.open_time + INTERVAL_30M_MS, cfg.display_tz)} "
                    f"ranging={st.is_ranging} flip={det.flip.direction if det.flip else '-'}"
                )

    ts, px = client.fetch_mark(book.symbol)
    result = det.on_tick(ts, px)
    for sig in result.signals:
        key = (sig.signal_time, sig.symbol, sig.side, sig.logic)
        if key in book.seen:
            continue
        book.seen.add(key)
        book.pending.append(sig)
        print_signal(sig, cfg)
        if token and chat:
            try:
                send_telegram(token, chat, format_signal_tg(sig, cfg))
            except Exception as exc:
                print(f"Telegram 信号发送失败: {exc}")
    for c in result.conflicts:
        print_conflict(c, cfg)

    still: list[Signal] = []
    for sig in book.pending:
        if ts >= sig.settle_time:
            sig.settle_px = px
            sig.settle_time = sig.signal_time + SETTLE_MS
            if sig.side == "LONG":
                sig.pnl_abs = px - sig.open_px
            else:
                sig.pnl_abs = sig.open_px - px
            sig.pnl_pct = sig.pnl_abs / sig.open_px if sig.open_px else 0.0
            if sig.pnl_abs > 0:
                sig.result = "胜"
            elif sig.pnl_abs < 0:
                sig.result = "负"
            else:
                sig.result = "平"
            apply_binary_payout(sig, cfg.payout_rate, cfg.stake)
            t = format_ts(ts, cfg.display_tz)
            print(
                f"[{t}] 结算 {sig.symbol} {sig.side} {sig.logic} {sig.result} "
                f"开={sig.open_px:.1f} 结={px:.1f} 支付盈亏={sig.payout_pnl:+.2f}"
            )
            if token and chat:
                try:
                    send_telegram(token, chat, format_settle_tg(sig, cfg))
                except Exception as exc:
                    print(f"Telegram 结算发送失败: {exc}")
        else:
            still.append(sig)
    book.pending[:] = still
