"""Reconnect supervisor for a live provider."""

import asyncio
import logging

try:
    from websockets.exceptions import ConnectionClosed
except ImportError:
    ConnectionClosed = ConnectionError

logger = logging.getLogger(__name__)


class LiveMarketDataService:
    def __init__(self, provider, *, initial_delay=1.0, max_delay=30.0, sleep=asyncio.sleep):
        if initial_delay < 0 or max_delay < initial_delay: raise ValueError("Invalid reconnect bounds")
        self.provider, self.initial_delay, self.max_delay, self._sleep = provider, initial_delay, max_delay, sleep
        self._running = False
        self._stop_event = asyncio.Event()

    async def start(self):
        if self._running: return
        self._running = True; self._stop_event.clear(); delay = self.initial_delay
        try:
            while self._running:
                try:
                    await self.provider.connect()
                    await self.provider.resubscribe()
                    await self.provider.wait_closed()
                except (OSError, ConnectionError, asyncio.TimeoutError, ConnectionClosed):
                    if not self._running: break
                    self.provider.health.reconnect_count += 1
                    await self.provider.disconnect()
                    sleeper = asyncio.create_task(self._sleep(delay))
                    stopper = asyncio.create_task(self._stop_event.wait())
                    done, pending = await asyncio.wait((sleeper, stopper), return_when=asyncio.FIRST_COMPLETED)
                    for task in pending: task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    delay = min(delay * 2, self.max_delay)
        finally:
            self._running = False
            await self.provider.disconnect()

    async def stop(self):
        self._running = False
        self._stop_event.set()
        await self.provider.disconnect()
