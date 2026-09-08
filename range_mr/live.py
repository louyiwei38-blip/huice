from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .binance_data import BinanceUMFutures
from .config import Config, INTERVAL_1M_MS, INTERVAL_30M_MS, SETTLE_MS
from .detector import Detector
from .models import Bar, DetectResult, Signal
from .live_stats import append_settlement, format_boot_stats, format_live_stats_tg, load_rows, summarize_live
from .replay import apply_binary_payout
from .stats import format_ts, print_conflict, print_signal
from .telegram_notify import format_settle_tg, format_signal_tg, load_telegram, send_telegram
from .trade import TradeBot


@dataclass
class SymbolBook:
    symbol: str
    cfg: Config
    det: Detector
    last_30m_open: int
    last_1m_open: int
    prev_1m_close: float
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
        hist_1m = client.fetch_closed_klines(symbol, "1m", limit=5)
        if not hist_1m:
            raise RuntimeError(f"{symbol} 1m 历史不足")
        st = det.structure
        print(
            f"LIVE {symbol} 已加载 {len(history)} 根30m | "
            f"检测=1m影线触及 | "
            f"盘整条件={'开' if scfg.require_ranging else '关'} | "
            f"盘整={st.is_ranging if st else None} | "
            f"箱体={st.range_low if st else 0:.1f}-{st.range_high if st else 0:.1f}"
        )
        books.append(
            SymbolBook(
                symbol=symbol,
                cfg=scfg,
                det=det,
                last_30m_open=history[-1].open_time,
                last_1m_open=hist_1m[-1].open_time,
                prev_1m_close=hist_1m[-1].close,
                pending=[],
                seen=set(),
            )
        )

    token, chat = load_telegram(cfg)
    if token and chat:
        print(f"Telegram 已连接 chat_id={chat}")
    else:
        print("未配置 Telegram：信号只打印在终端。请设置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 或 data/telegram.json")

    trader = TradeBot.from_config(cfg)
    trade_ready = False
    if trader:
        try:
            info = trader.connect()
            trade_ready = True
            print(
                f"下单面板已连接 {trader.base_url} | "
                f"带单#{info['leader_id']} {info['leader_name']} | "
                f"{trader.order_amount}U / 30m / 赔付 {trader.payout_ratio}"
            )
        except Exception as exc:
            print(f"下单面板暂时连不上，出信号时会重试: {exc}")
    else:
        print("未开启自动下单：复制 data/trade.json.example 为 data/trade.json 并填写面板账号")

    live_stake = float(trader.order_amount) if trader else cfg.stake
    boot_lines = [
        "Range MR V1.1 已启动",
        "标的: " + ", ".join(symbols),
        f"支付率 {cfg.payout_rate:.0%}",
        f"盘整条件: {'开' if cfg.require_ranging else '关'}",
        "检测: 1m影线触及（盘整关，与回放②一致）",
        f"结算本金: {live_stake:g}U",
    ]
    boot_stats = format_boot_stats(cfg.display_tz, live_stake)
    if boot_stats:
        boot_lines.append(boot_stats)
    if trader:
        status = "已连接" if trade_ready else "待重试"
        boot_lines.append(
            f"自动下单: {status} {trader.order_amount}U / 30分钟 / {trader.base_url}"
        )
    else:
        boot_lines.append("自动下单: 关闭")
    if token and chat:
        try:
            send_telegram(token, chat, "\n".join(boot_lines))
        except Exception as exc:
            print(f"Telegram 启动消息失败: {exc}")

    while True:
        try:
            for book in books:
                _poll(client, book, token, chat, trader, live_stake)
        except KeyboardInterrupt:
            print("停止 live")
            return
        except Exception as exc:
            print(f"live 轮询异常: {exc}")
        time.sleep(cfg.live_poll_sec)


def _handle_detect(
    book: SymbolBook,
    result: DetectResult,
    token: str,
    chat: str,
    trader: TradeBot | None,
) -> None:
    cfg = book.cfg
    for sig in result.signals:
        key = (sig.signal_time, sig.symbol, sig.side, sig.logic)
        if key in book.seen:
            continue
        book.seen.add(key)
        trigger_ts = int(time.time() * 1000)
        sig.signal_time = trigger_ts
        sig.settle_time = trigger_ts + SETTLE_MS
        book.pending.append(sig)
        print_signal(sig, cfg)
        trade_note = ""
        if trader:
            try:
                placed = trader.place(sig)
                trade_note = (
                    f"自动下单成功 {trader.order_amount}U 30m {sig.symbol} "
                    f"{'看涨' if sig.side == 'LONG' else '看跌'}"
                )
                extra = placed.get("id") or placed.get("orderId") or placed.get("signalId")
                if extra:
                    trade_note += f" id={extra}"
                print(trade_note, flush=True)
            except Exception as exc:
                trade_note = f"自动下单失败: {exc}"
                print(trade_note, flush=True)
        if token and chat:
            try:
                text = format_signal_tg(sig, cfg)
                if trade_note:
                    text += "\n" + trade_note
                send_telegram(token, chat, text)
            except Exception as exc:
                print(f"Telegram 信号发送失败: {exc}")
    for c in result.conflicts:
        print_conflict(c, cfg)


def _poll(
    client: BinanceUMFutures,
    book: SymbolBook,
    token: str,
    chat: str,
    trader: TradeBot | None,
    live_stake: float,
) -> None:
    cfg = book.cfg
    det = book.det
    ts, px = client.fetch_mark(book.symbol)
    m1 = client.fetch_recent_klines(book.symbol, "1m", limit=8)
    bars_30 = client.fetch_closed_klines(book.symbol, "30m", limit=8)

    def apply_30m_up_to(open_ts: int) -> None:
        for bar in bars_30:
            if bar.open_time <= book.last_30m_open:
                continue
            if bar.open_time + INTERVAL_30M_MS > open_ts:
                continue
            det.on_30m_close(bar)
            book.last_30m_open = bar.open_time
            st = det.structure
            stats = det.pop_filter_stats()
            if st:
                print(
                    f"{book.symbol} 30m收盘 {format_ts(bar.open_time + INTERVAL_30M_MS, cfg.display_tz)} "
                    f"ranging={st.is_ranging} flip={det.flip.direction if det.flip else '-'} "
                    f"box={st.range_low:.1f}-{st.range_high:.1f} "
                    f"扫描={stats['scan']} 候选={stats['candidates']} "
                    f"当根丢={stats['with_bar']} 突破窗={stats['flip_only']} "
                    f"冷却={stats['cooldown']} 发出={stats['emitted']}",
                    flush=True,
                )

    closed_1m = [b for b in m1 if b.open_time + INTERVAL_1M_MS <= ts]
    forming = None
    if m1 and m1[-1].open_time + INTERVAL_1M_MS > ts:
        forming = m1[-1]

    for bar in closed_1m:
        if bar.open_time <= book.last_1m_open:
            continue
        apply_30m_up_to(bar.open_time)
        result = det.on_1m(bar, book.prev_1m_close)
        book.prev_1m_close = bar.close
        book.last_1m_open = bar.open_time
        _handle_detect(book, result, token, chat, trader)

    if forming is not None and forming.open_time > book.last_1m_open:
        apply_30m_up_to(forming.open_time)
        forming = Bar(
            open_time=forming.open_time,
            close_time=forming.close_time,
            open=forming.open,
            high=max(forming.high, px),
            low=min(forming.low, px),
            close=forming.close,
            volume=forming.volume,
        )
        result = det.on_1m(forming, book.prev_1m_close)
        _handle_detect(book, result, token, chat, trader)

    still: list[Signal] = []
    for sig in book.pending:
        if ts >= sig.settle_time:
            sig.settle_px = px
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
            apply_binary_payout(sig, cfg.payout_rate, live_stake)
            append_settlement(sig, live_stake, cfg.display_tz)
            today = format_ts(sig.settle_time, cfg.display_tz)[:10]
            extra = format_live_stats_tg(summarize_live(load_rows(), today), live_stake)
            t = format_ts(ts, cfg.display_tz)
            print(
                f"[{t}] 结算 {sig.symbol} {sig.side} {sig.logic} {sig.result} "
                f"开={sig.open_px:.1f} 结={px:.1f} 支付盈亏={sig.payout_pnl:+.2f}",
                flush=True,
            )
            print(extra.replace("<b>", "").replace("</b>", ""), flush=True)
            if token and chat:
                try:
                    send_telegram(token, chat, format_settle_tg(sig, cfg, live_stake, extra))
                except Exception as exc:
                    print(f"Telegram 结算发送失败: {exc}")
        else:
            still.append(sig)
    book.pending[:] = still
