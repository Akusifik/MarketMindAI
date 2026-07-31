"""Read-only live Bybit order-flow analysis runner.

This tool only collects trusted normalized market data and delegates all
interpretation to :func:`analysis.order_flow_analysis.analyze_order_flow`.
"""

import argparse
import asyncio
import sys
from collections import deque
from contextlib import suppress
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis.order_flow_analysis import analyze_order_flow  # noqa: E402
from config import (  # noqa: E402
    BYBIT_WS_URL,
    LIVE_MESSAGE_TIMEOUT,
    LIVE_ORDER_BOOK_DEPTH,
    LIVE_RECONNECT_INITIAL_DELAY,
    LIVE_RECONNECT_MAX_DELAY,
)
from exchanges.live import BybitWebSocketProvider, LiveMarketDataService  # noqa: E402
from orderflow import OrderBookSnapshot, Trade  # noqa: E402


def _freeze(value):
    """Build a stable, hashable identity without discarding event context."""
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def unique_anomalies(anomalies):
    """Remove only exact duplicate structured anomaly objects, preserving order."""
    unique, seen = [], set()
    for anomaly in anomalies or ():
        identity = _freeze(anomaly)
        if identity not in seen:
            seen.add(identity)
            unique.append(anomaly)
    return unique


def format_anomaly(anomaly):
    """Render one structured anomaly compactly without exposing its raw dict."""
    anomaly_type = str(anomaly.get("type", "UNKNOWN"))
    parts = [anomaly_type]
    side, price = anomaly.get("side"), anomaly.get("price", anomaly.get("price_level"))
    if side is not None and price is not None:
        parts.append(f"{side} @ {price:g}" if isinstance(price, (int, float)) else f"{side} @ {price}")
    elif side is not None:
        parts.append(str(side))
    elif price is not None:
        parts.append(f"@ {price:g}" if isinstance(price, (int, float)) else f"@ {price}")
    strength = anomaly.get("strength", anomaly.get("confidence"))
    if strength is not None:
        parts.append(f"strength={strength:g}" if isinstance(strength, (int, float)) else f"strength={strength}")
    evidence = anomaly.get("evidence") or anomaly.get("reasons") or ()
    if isinstance(evidence, str):
        evidence = [evidence]
    if evidence:
        reason = str(evidence[0]).strip()
        if reason:
            parts.append(reason if len(reason) <= 100 else reason[:97].rstrip() + "...")
    return " | ".join(parts)


class LiveOrderFlowRunner:
    """Maintain bounded, generation-isolated inputs for the analysis engine."""

    def __init__(
        self,
        provider,
        symbol="BTCUSDT",
        *,
        analysis_interval=2.0,
        trade_window=60.0,
        snapshot_history=200,
        max_trades=50_000,
        analyzer=analyze_order_flow,
        output=print,
    ):
        if analysis_interval <= 0 or trade_window <= 0:
            raise ValueError("analysis_interval and trade_window must be positive")
        if snapshot_history < 2 or max_trades < 1:
            raise ValueError("snapshot_history must be at least 2 and max_trades must be positive")
        self.provider = provider
        self.symbol = symbol.upper()
        self.analysis_interval = analysis_interval
        self.trade_window = trade_window
        self.snapshots = deque(maxlen=snapshot_history)
        self.trades = deque(maxlen=max_trades)
        self.analyzer = analyzer
        self.output = output
        self.generation = None
        self.analysis_count = 0

    def _health(self):
        return self.provider.health.symbols.get(self.symbol)

    def _reset_if_untrusted_or_changed(self):
        health = self._health()
        trusted = bool(self.provider.health.connected and health and health.synchronized)
        current_generation = health.generation if health else None
        if not trusted:
            self.snapshots.clear()
            self.trades.clear()
            self.generation = None
            return False
        if self.generation != current_generation:
            self.snapshots.clear()
            self.trades.clear()
            self.generation = current_generation
        return True

    def record(self, event):
        """Record an event only while its symbol generation is trusted."""
        if not self._reset_if_untrusted_or_changed():
            return False
        if event.symbol != self.symbol:
            return False
        # Re-read after accepting the event so a concurrent resync cannot tag it
        # with an obsolete generation.
        health = self._health()
        if not health or not health.synchronized or health.generation != self.generation:
            self._reset_if_untrusted_or_changed()
            return False
        if isinstance(event, OrderBookSnapshot):
            if self.snapshots and (
                event.timestamp <= self.snapshots[-1][1].timestamp
                or (
                    event.sequence is not None
                    and self.snapshots[-1][1].sequence is not None
                    and event.sequence <= self.snapshots[-1][1].sequence
                )
            ):
                return False
            self.snapshots.append((self.generation, event))
            return True
        if isinstance(event, Trade):
            self.trades.append((self.generation, event))
            self._prune_trades(event.timestamp)
            return True
        return False

    def _prune_trades(self, reference_time):
        cutoff = reference_time.timestamp() - self.trade_window
        while self.trades and self.trades[0][1].timestamp.timestamp() < cutoff:
            self.trades.popleft()

    def analyze(self):
        """Analyze the latest trusted state, or return ``None`` while waiting."""
        if not self._reset_if_untrusted_or_changed() or not self.snapshots:
            return None
        health = self._health()
        generation = self.generation
        current = self.snapshots[-1][1]
        self._prune_trades(current.timestamp)
        history = [item for item_generation, item in list(self.snapshots)[:-1] if item_generation == generation]
        trades = sorted(
            (item for item_generation, item in self.trades if item_generation == generation and item.timestamp <= current.timestamp),
            key=lambda item: item.timestamp,
        )
        # Final trust check immediately before handing data to the engine.
        if not (self.provider.health.connected and health.synchronized and health.generation == generation):
            self._reset_if_untrusted_or_changed()
            return None
        result = self.analyzer(current, trades, history)
        self.analysis_count += 1
        return result

    async def consume(self):
        async for event in self.provider.events():
            self.record(event)

    async def analysis_loop(self, stop_event):
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.analysis_interval)
                break
            except asyncio.TimeoutError:
                result = self.analyze()
                if result is not None:
                    self.print_summary(result)

    def print_summary(self, result):
        health = self._health()
        book, flow = result["book_state"], result["trade_flow"]
        mid = book.get("mid_price")
        bid_walls = [wall for wall in result["liquidity_walls"] if wall["side"] == "BID"]
        ask_walls = [wall for wall in result["liquidity_walls"] if wall["side"] == "ASK"]
        nearest_bid = max(bid_walls, key=lambda wall: wall["price"], default=None)
        nearest_ask = min(ask_walls, key=lambda wall: wall["price"], default=None)
        wall_text = lambda wall: f"{wall['price']:g} ({wall['quantity']:g})" if wall else "none"
        absorption = result["absorption"]["type"]
        sweep = result["sweeps"][0]["type"] if result["sweeps"] else "NONE"
        self.output(f"\nORDER FLOW {self.symbol}")
        self.output(f"Book: {book['state']} | imbalance={book['imbalance']:+.3f} | mid={mid}")
        self.output(
            f"Trade Flow: {flow['pressure']} | buy={flow['buy_volume']:.6g} "
            f"sell={flow['sell_volume']:.6g} delta={flow['delta']:+.6g}"
        )
        self.output(f"Liquidity: bid={wall_text(nearest_bid)} | ask={wall_text(nearest_ask)}")
        self.output(
            f"Analysis: {result['bias']} strength={result['strength']:.0f} | "
            f"absorption={absorption} sweep={sweep}"
        )
        anomalies = unique_anomalies(result.get("anomalies"))
        self.output("Anomalies:")
        if anomalies:
            for anomaly in anomalies:
                self.output(f"- {format_anomaly(anomaly)}")
        else:
            self.output("- NONE")
        self.output(
            f"Health: synchronized={health.synchronized} generation={health.generation} "
            f"reconnects={self.provider.health.reconnect_count} gaps={health.sequence_gap_count}"
        )


async def run_live_order_flow(
    *, symbol, duration, analysis_interval, trade_window, snapshot_history,
    max_trades, url=BYBIT_WS_URL, provider=None, service=None, output=print,
):
    provider = provider or BybitWebSocketProvider(
        url, depth=LIVE_ORDER_BOOK_DEPTH, message_timeout=LIVE_MESSAGE_TIMEOUT
    )
    service = service or LiveMarketDataService(
        provider,
        initial_delay=LIVE_RECONNECT_INITIAL_DELAY,
        max_delay=LIVE_RECONNECT_MAX_DELAY,
    )
    runner = LiveOrderFlowRunner(
        provider, symbol, analysis_interval=analysis_interval,
        trade_window=trade_window, snapshot_history=snapshot_history,
        max_trades=max_trades, output=output,
    )
    await provider.subscribe_order_book(runner.symbol)
    await provider.subscribe_trades(runner.symbol)
    output(f"WAITING: {runner.symbol} trusted order-book snapshot")
    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(service.start(), name="order-flow-service"),
        asyncio.create_task(runner.consume(), name="order-flow-consumer"),
        asyncio.create_task(runner.analysis_loop(stop_event), name="order-flow-analysis"),
    ]
    try:
        if duration is None:
            await tasks[0]
        else:
            await asyncio.sleep(duration)
    finally:
        stop_event.set()
        await service.stop()
        for task in tasks[1:]:
            task.cancel()
        await asyncio.gather(*tasks[1:], return_exceptions=True)
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(tasks[0], timeout=5.0)
        if not tasks[0].done():
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
    return runner


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Live read-only Bybit order-flow analysis.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--duration", type=float, default=30.0, help="Seconds; use 0 for Ctrl+C mode.")
    parser.add_argument("--analysis-interval", type=float, default=2.0)
    parser.add_argument("--trade-window", type=float, default=60.0)
    parser.add_argument("--snapshot-history", type=int, default=200)
    parser.add_argument("--max-trades", type=int, default=50_000)
    parser.add_argument("--url", default=BYBIT_WS_URL)
    args = parser.parse_args(argv)
    if args.duration < 0 or args.analysis_interval <= 0 or args.trade_window <= 0:
        parser.error("duration must be non-negative; intervals must be positive")
    if args.snapshot_history < 2 or args.max_trades < 1:
        parser.error("snapshot history must be at least 2 and max trades must be positive")
    args.duration = None if args.duration == 0 else args.duration
    return args


def main():
    args = parse_args()
    try:
        asyncio.run(run_live_order_flow(**vars(args)))
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()
