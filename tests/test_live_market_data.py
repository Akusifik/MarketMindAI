import asyncio
import json
import unittest

from exchanges.live.bybit_ws import BybitBookMessage, BybitParser, BybitWebSocketProvider
from exchanges.live.service import LiveMarketDataService
from orderflow import OrderBookSnapshot, Trade


def book(symbol="BTCUSDT", kind="snapshot", u=18521288, seq=7961638724, bids=None, asks=None):
    return {"topic": f"orderbook.50.{symbol}", "type": kind, "ts": 1672304484978,
            "data": {"s": symbol, "b": bids if bids is not None else [["100", "2"], ["99", "1"]],
                     "a": asks if asks is not None else [["101", "3"], ["102", "1"]], "u": u, "seq": seq}}


def trade(side="Buy"):
    return {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": 1672304486868,
            "data": [{"T": 1672304486865, "s": "BTCUSDT", "S": side, "v": "0.001", "p": "100", "i": side}]}


class FakeSocket:
    def __init__(self, auto_ack=True):
        self.incoming = asyncio.Queue(); self.sent = []; self.closed = False; self.auto_ack = auto_ack

    async def send(self, raw):
        payload = json.loads(raw); self.sent.append(payload)
        if self.auto_ack and payload.get("op") in {"subscribe", "unsubscribe"}:
            await self.push({"success": True, "ret_msg": "", "op": payload["op"], "req_id": payload["req_id"]})
        if self.auto_ack and payload.get("op") == "ping":
            await self.push({"success": True, "ret_msg": "pong", "op": "ping", "req_id": payload["req_id"]})

    async def recv(self):
        item = await self.incoming.get()
        if isinstance(item, BaseException): raise item
        return item
    async def push(self, payload): await self.incoming.put(payload if isinstance(payload, str) else json.dumps(payload))
    async def close(self): self.closed = True


class ParserTests(unittest.TestCase):
    def test_realistic_book_and_nonconsecutive_ids(self):
        event = BybitParser.parse(book())[0]
        later = BybitParser.parse(book(kind="delta", u=18521310, seq=7961638800, bids=[["100", "4"]], asks=[]))[0]
        self.assertIsInstance(event, BybitBookMessage)
        self.assertGreater(later.update_id, event.update_id + 1)

    def test_trade_taker_side_and_timestamp(self):
        buy, sell = BybitParser.parse(trade("Buy"))[0], BybitParser.parse(trade("Sell"))[0]
        self.assertIsInstance(buy, Trade); self.assertEqual((buy.side, sell.side), ("BUY", "SELL"))
        self.assertEqual(buy.timestamp.tzinfo.utcoffset(buy.timestamp).total_seconds(), 0)

    def test_malformed(self):
        for payload in ("bad", {}, {"topic": "orderbook.50.BTCUSDT", "data": []}):
            with self.assertRaises(ValueError): BybitParser.parse(payload)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket = FakeSocket()
        self.provider = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=self.socket), ack_timeout=.1, heartbeat_interval=100)
        await self.provider.connect()

    async def asyncTearDown(self): await self.provider.disconnect()

    async def settle(self): await asyncio.sleep(.02)

    async def test_interleaved_symbols_are_independent(self):
        await self.provider.subscribe_order_book("BTCUSDT"); await self.provider.subscribe_order_book("ETHUSDT")
        await self.socket.push(book("BTCUSDT")); await self.socket.push(book("ETHUSDT", u=50, seq=80, bids=[["200", "1"]], asks=[["201", "1"]]))
        await self.settle()
        btc, eth = self.provider._books["BTCUSDT"], self.provider._books["ETHUSDT"]
        self.assertEqual(btc.state.symbol, "BTCUSDT"); self.assertEqual(eth.state.symbol, "ETHUSDT")
        self.assertIsNot(btc.state, eth.state)
        await self.socket.push(book("BTCUSDT", "delta", 18521300, 7961638700, [], []))
        await self.settle()
        self.assertFalse(self.provider.health.symbols["BTCUSDT"].synchronized)
        self.assertTrue(self.provider.health.symbols["ETHUSDT"].synchronized)

    async def test_nonconsecutive_duplicate_stale_restart_and_fresh_snapshot(self):
        await self.provider.subscribe_order_book("BTCUSDT")
        await self.socket.push(book()); await self.settle()
        await self.socket.push(book(kind="delta", u=18521310, seq=7961638800, bids=[["100", "4"]], asks=[]))
        await self.settle(); health = self.provider.health.symbols["BTCUSDT"]
        self.assertTrue(health.synchronized); self.assertEqual(health.last_update_id, 18521310)
        await self.socket.push(book(kind="delta", u=18521310, seq=7961638800, bids=[], asks=[]))
        await self.socket.push(book(kind="delta", u=18521300, seq=7961638700, bids=[], asks=[]))
        await self.settle(); self.assertTrue(health.synchronized)
        generation = health.generation
        await self.socket.push(book(kind="delta", u=1, seq=1, bids=[], asks=[])); await self.settle()
        self.assertFalse(health.synchronized); self.assertEqual(health.generation, generation + 1)
        await self.socket.push(book(kind="snapshot", u=1, seq=9000000000)); await self.settle()
        self.assertTrue(health.synchronized); self.assertEqual(health.last_update_id, 1)

    async def test_old_generation_queued_states_are_discarded(self):
        await self.provider.subscribe_order_book("BTCUSDT")
        await self.socket.push(book()); await self.socket.push(book(kind="delta", u=18521310, seq=7961638800, bids=[["100", "4"]], asks=[]))
        await self.settle()
        await self.socket.push(book(kind="delta", u=18521311, seq=7000000000, bids=[], asks=[])); await self.settle()
        with self.assertRaises(asyncio.TimeoutError): await asyncio.wait_for(self.provider.next_event(), .03)
        await self.socket.push(book(kind="snapshot", u=1, seq=9000000000)); await self.settle()
        current = await asyncio.wait_for(self.provider.next_event(), .1)
        self.assertIsInstance(current, OrderBookSnapshot); self.assertEqual(current.sequence, 1)

    async def test_ack_failure_mismatch_delay_and_timeout(self):
        await self.provider.disconnect(); socket = FakeSocket(auto_ack=False)
        provider = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=socket), ack_timeout=.03, heartbeat_interval=100)
        await provider.connect()
        task = asyncio.create_task(provider.subscribe_order_book("BTCUSDT")); await asyncio.sleep(0)
        request = socket.sent[-1]
        await socket.push({"success": True, "op": "subscribe", "req_id": "wrong"})
        await asyncio.sleep(.005); self.assertFalse(task.done())
        await socket.push({"success": False, "ret_msg": "denied", "op": "subscribe", "req_id": request["req_id"]})
        with self.assertRaises(ConnectionError): await task
        with self.assertRaises(asyncio.TimeoutError): await provider.subscribe_trades("BTCUSDT")
        await provider.disconnect(); self.assertFalse(provider._pending)

    async def test_delayed_ack_and_cancelled_ack_wait(self):
        await self.provider.disconnect(); socket = FakeSocket(auto_ack=False)
        provider = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=socket), ack_timeout=.1, heartbeat_interval=100)
        await provider.connect()
        delayed = asyncio.create_task(provider.subscribe_order_book("BTCUSDT")); await asyncio.sleep(.01)
        request = socket.sent[-1]; self.assertFalse(delayed.done())
        await socket.push({"success": True, "op": "subscribe", "req_id": request["req_id"]})
        await delayed
        cancelled = asyncio.create_task(provider.subscribe_trades("BTCUSDT")); await asyncio.sleep(0)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError): await cancelled
        self.assertFalse(provider._pending)
        await provider.disconnect()

    async def test_heartbeat_success_and_quiet_timeout(self):
        await self.provider.disconnect(); socket = FakeSocket()
        provider = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=socket), message_timeout=.05, heartbeat_interval=.01)
        await provider.connect(); await asyncio.sleep(.06)
        self.assertIsNotNone(provider.health.last_ping_time); self.assertIsNotNone(provider.health.last_pong_time)
        socket.auto_ack = False
        with self.assertRaises(asyncio.TimeoutError): await provider.wait_closed()
        await provider.disconnect()

    async def test_disconnect_is_idempotent_and_leak_free(self):
        await self.provider.disconnect(); await self.provider.disconnect()
        self.assertTrue(self.socket.closed); self.assertFalse(self.provider._pending)
        self.assertIsNone(self.provider._reader_task); self.assertIsNone(self.provider._heartbeat_task)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_backoff_and_no_duplicate_subscriptions(self):
        class Stub:
            def __init__(self):
                from exchanges.live.base import ConnectionHealth
                self.health = ConnectionHealth(); self.connects = self.subscribes = self.disconnects = 0
            async def connect(self): self.connects += 1
            async def resubscribe(self): self.subscribes += 1
            async def wait_closed(self): raise ConnectionError()
            async def disconnect(self): self.disconnects += 1
        stub, delays = Stub(), []
        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 3: service._running = False
        service = LiveMarketDataService(stub, initial_delay=1, max_delay=2, sleep=sleep)
        await service.start()
        self.assertEqual(delays, [1, 2, 2]); self.assertEqual(stub.subscribes, stub.connects)
        self.assertEqual(stub.health.reconnect_count, 3)

    async def test_stop_during_read_and_backoff(self):
        socket = FakeSocket()
        provider = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=socket), heartbeat_interval=100)
        service = LiveMarketDataService(provider, initial_delay=0, max_delay=0)
        task = asyncio.create_task(service.start()); await asyncio.sleep(.02)
        await service.stop(); await asyncio.wait_for(task, .1)
        await service.stop(); self.assertTrue(socket.closed)

    async def test_real_provider_failed_resync_forces_clean_multisymbol_reconnect(self):
        first, second, third = FakeSocket(), FakeSocket(), FakeSocket()
        sockets = [first, second, third]
        async def factory(_): return sockets.pop(0) if sockets else second
        provider = BybitWebSocketProvider("test", websocket_factory=factory, ack_timeout=.03, heartbeat_interval=100)
        await provider.subscribe_order_book("BTCUSDT"); await provider.subscribe_order_book("ETHUSDT")
        service = LiveMarketDataService(provider, initial_delay=0, max_delay=0)
        runner = asyncio.create_task(service.start())
        for _ in range(20):
            if first.sent: break
            await asyncio.sleep(.005)
        first_reader = provider._reader_task
        self.assertEqual(len([item for item in first.sent if item.get("op") == "subscribe"]), 1)
        await first.push(book("BTCUSDT")); await first.push(book("ETHUSDT", u=50, seq=80, bids=[["200", "1"]], asks=[["201", "1"]]))
        await asyncio.sleep(.02)
        first.auto_ack = False
        await first.push(book("BTCUSDT", "delta", 18521300, 7000000000, [], []))
        for _ in range(40):
            if provider.health.reconnect_count and second.sent and provider._ready_books == {"BTCUSDT", "ETHUSDT"}: break
            await asyncio.sleep(.005)
        self.assertEqual(provider.health.reconnect_count, 1)
        self.assertTrue(first.closed)
        self.assertEqual(len([item for item in first.sent if item.get("op") == "subscribe"]), 1)
        self.assertEqual(len([item for item in second.sent if item.get("op") == "subscribe"]), 1)
        self.assertFalse(provider.health.symbols["BTCUSDT"].synchronized)
        self.assertFalse(provider.health.symbols["ETHUSDT"].synchronized)
        with self.assertRaises(asyncio.TimeoutError): await asyncio.wait_for(provider.next_event(), .02)
        await second.push(book("BTCUSDT", u=900, seq=9000)); await asyncio.sleep(.02)
        self.assertTrue(provider.health.symbols["BTCUSDT"].synchronized)
        self.assertFalse(provider.health.symbols["ETHUSDT"].synchronized)
        self.assertEqual(provider._reader_task.get_name(), "bybit-reader")
        self.assertIsNot(provider._reader_task, first_reader); self.assertTrue(first_reader.done())

        await second.push(book("ETHUSDT", u=901, seq=9001, bids=[["200", "1"]], asks=[["201", "1"]]))
        await asyncio.sleep(.01); second.auto_ack = False
        await second.push(book("BTCUSDT", "delta", 901, 8000, [], []))
        for _ in range(40):
            if provider.health.reconnect_count == 2 and third.sent and provider._ready_books == {"BTCUSDT", "ETHUSDT"}: break
            await asyncio.sleep(.005)
        self.assertEqual(provider.health.reconnect_count, 2)
        self.assertTrue(second.closed)
        self.assertEqual(len([item for item in second.sent if item.get("op") == "subscribe"]), 1)
        self.assertFalse(provider.health.symbols["BTCUSDT"].synchronized)
        self.assertFalse(provider.health.symbols["ETHUSDT"].synchronized)
        await service.stop(); await asyncio.wait_for(runner, .1)
        self.assertFalse(provider._pending); self.assertFalse(provider._resync_tasks)

    async def test_stop_during_resync_and_real_backoff(self):
        socket = FakeSocket(); provider = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=socket), ack_timeout=1, heartbeat_interval=100)
        await provider.subscribe_order_book("BTCUSDT")
        service = LiveMarketDataService(provider, initial_delay=10, max_delay=10)
        runner = asyncio.create_task(service.start())
        while "BTCUSDT" not in provider._ready_books: await asyncio.sleep(.001)
        await socket.push(book()); await asyncio.sleep(.01)
        socket.auto_ack = False
        await socket.push(book(kind="delta", u=18521300, seq=7000000000, bids=[], asks=[]))
        while not provider._resync_tasks: await asyncio.sleep(.001)
        await service.stop(); await asyncio.wait_for(runner, .1)
        self.assertFalse(provider._pending); self.assertFalse(provider._resync_tasks)

        failing = FakeSocket(); provider2 = BybitWebSocketProvider("test", websocket_factory=lambda _: asyncio.sleep(0, result=failing), heartbeat_interval=100)
        service2 = LiveMarketDataService(provider2, initial_delay=10, max_delay=10)
        runner2 = asyncio.create_task(service2.start())
        while not provider2.health.connected: await asyncio.sleep(.001)
        await failing.incoming.put(ConnectionError())
        while provider2.health.reconnect_count == 0: await asyncio.sleep(.001)
        await service2.stop(); await asyncio.wait_for(runner2, .1)
        self.assertFalse(provider2._pending)


if __name__ == "__main__": unittest.main()
