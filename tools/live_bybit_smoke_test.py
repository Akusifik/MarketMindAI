"""Controlled Bybit V5 public market-data smoke test (no trading)."""

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    BYBIT_WS_URL,
    LIVE_MESSAGE_TIMEOUT,
    LIVE_ORDER_BOOK_DEPTH,
    LIVE_RECONNECT_INITIAL_DELAY,
    LIVE_RECONNECT_MAX_DELAY,
)
from exchanges.live import BybitWebSocketProvider, LiveMarketDataService  # noqa: E402
from orderflow import OrderBookSnapshot, Trade, calculate_order_book_metrics  # noqa: E402


SYMBOL = "BTCUSDT"


@dataclass
class SmokeState:
    book: OrderBookSnapshot | None = None
    latest_trade: Trade | None = None
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    synchronized_announced: bool = False
    connected_announced: bool = False

    def record(self, event):
        if isinstance(event, OrderBookSnapshot):
            self.book = event
        elif isinstance(event, Trade):
            self.latest_trade = event
            if event.side == "BUY":
                self.buy_volume += event.quantity
            elif event.side == "SELL":
                self.sell_volume += event.quantity

    def take_flow(self):
        buy, sell = self.buy_volume, self.sell_volume
        self.buy_volume = self.sell_volume = 0.0
        return buy, sell


async def consume(provider, state):
    async for event in provider.events():
        state.record(event)


def print_summary(provider, state, *, final=False):
    health = provider.health
    symbol_health = health.symbols.get(SYMBOL)
    buy, sell = state.take_flow()
    label = "FINAL" if final else "STATUS"
    print(f"\n{label}")
    print(f"Connected: {health.connected} | Reconnects: {health.reconnect_count}")
    if symbol_health is None:
        print(f"{SYMBOL}: awaiting subscription health")
        return
    print(
        f"{SYMBOL}: synchronized={symbol_health.synchronized} "
        f"generation={symbol_health.generation} gaps={symbol_health.sequence_gap_count}"
    )
    print(f"Update ID: {symbol_health.last_update_id} | Seq: {symbol_health.last_sequence}")
    if state.book is not None and symbol_health.synchronized:
        metrics = calculate_order_book_metrics(state.book, top_n=5)
        print(
            f"Best bid: {metrics['best_bid']} | Best ask: {metrics['best_ask']} | "
            f"Spread: {metrics['spread']}"
        )
        print(
            f"Depth bid/ask: {metrics['total_bid_depth']:.6g}/{metrics['total_ask_depth']:.6g} | "
            f"Top-5: {metrics['top_n_bid_depth']:.6g}/{metrics['top_n_ask_depth']:.6g}"
        )
    else:
        print("Book: awaiting trusted snapshot")
    print(f"Recent trade volume BUY/SELL: {buy:.6g}/{sell:.6g} | Delta: {buy - sell:.6g}")
    if state.latest_trade is not None:
        trade = state.latest_trade
        print(f"Latest trade: {trade.side} {trade.quantity:g} @ {trade.price:g} ({trade.timestamp.isoformat()})")
    else:
        print("Latest trade: awaiting trade")


async def run_smoke_test(duration, interval, url):
    provider = BybitWebSocketProvider(
        url,
        depth=LIVE_ORDER_BOOK_DEPTH,
        message_timeout=LIVE_MESSAGE_TIMEOUT,
    )
    service = LiveMarketDataService(
        provider,
        initial_delay=LIVE_RECONNECT_INITIAL_DELAY,
        max_delay=LIVE_RECONNECT_MAX_DELAY,
    )
    state = SmokeState()
    await provider.subscribe_order_book(SYMBOL)
    await provider.subscribe_trades(SYMBOL)
    service_task = asyncio.create_task(service.start(), name="smoke-service")
    consumer_task = asyncio.create_task(consume(provider, state), name="smoke-consumer")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    prior_reconnects = 0
    try:
        while loop.time() < deadline:
            await asyncio.sleep(min(interval, max(0.0, deadline - loop.time())))
            if provider.health.connected and not state.connected_announced:
                print("CONNECTED")
                state.connected_announced = True
            symbol_health = provider.health.symbols.get(SYMBOL)
            if symbol_health and symbol_health.synchronized and not state.synchronized_announced:
                print(f"{SYMBOL} BOOK SYNCHRONIZED")
                state.synchronized_announced = True
            if provider.health.reconnect_count > prior_reconnects:
                print(
                    "RECOVERY: connection/heartbeat/subscription/resync failure "
                    f"triggered reconnect #{provider.health.reconnect_count}"
                )
                prior_reconnects = provider.health.reconnect_count
                state.connected_announced = False
                state.synchronized_announced = False
            if service_task.done():
                error = service_task.exception()
                if error is not None:
                    raise RuntimeError("Live market-data service stopped unexpectedly") from error
                raise RuntimeError("Live market-data service exited before the smoke-test deadline")
            print_summary(provider, state)
    finally:
        await service.stop()
        consumer_task.cancel()
        await asyncio.gather(consumer_task, return_exceptions=True)
        try:
            await asyncio.wait_for(service_task, timeout=5.0)
        except asyncio.TimeoutError:
            service_task.cancel()
            await asyncio.gather(service_task, return_exceptions=True)
        print_summary(provider, state, final=True)
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        print(f"Pending asyncio tasks: {len([task for task in pending if not task.done()])}")


def parse_args():
    parser = argparse.ArgumentParser(description="Smoke-test Bybit V5 BTCUSDT public market data.")
    parser.add_argument("--duration", type=float, default=30.0, help="Run duration in seconds (default: 30).")
    parser.add_argument("--interval", type=float, default=5.0, help="Summary interval in seconds (default: 5).")
    parser.add_argument("--url", default=BYBIT_WS_URL, help="Bybit public linear WebSocket URL.")
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0:
        parser.error("--duration and --interval must be positive")
    return args


def main():
    args = parse_args()
    try:
        asyncio.run(run_smoke_test(args.duration, args.interval, args.url))
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as error:
        print(f"\nSMOKE TEST FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
