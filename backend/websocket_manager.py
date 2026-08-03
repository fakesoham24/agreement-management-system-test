"""
WebSocket Connection Manager — Real-Time Updates
Manages authenticated WebSocket connections and broadcasts events to all connected clients.
Uses FastAPI's built-in WebSocket support — no external dependencies.
"""
import json
import asyncio
import logging
from typing import Dict, Set, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections per user.
    Thread-safe broadcasting via asyncio event loop.
    Memory footprint: ~1-2 KB per connection.
    """

    def __init__(self):
        # { user_id: set(WebSocket) } — supports multiple tabs per user
        self._connections: Dict[int, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        # Reference to the main uvicorn event loop — captured on first connect()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket, user_id: int):
        """Accept a WebSocket connection and register it."""
        await websocket.accept()
        # Capture the main event loop (this runs inside uvicorn's async context)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = set()
            self._connections[user_id].add(websocket)
        logger.info(f"WebSocket connected: user_id={user_id} (total connections: {self.connection_count})")

    async def disconnect(self, websocket: WebSocket, user_id: int):
        """Remove a WebSocket connection."""
        async with self._lock:
            if user_id in self._connections:
                self._connections[user_id].discard(websocket)
                if not self._connections[user_id]:
                    del self._connections[user_id]
        logger.info(f"WebSocket disconnected: user_id={user_id} (total connections: {self.connection_count})")

    @property
    def connection_count(self) -> int:
        """Total number of active WebSocket connections."""
        return sum(len(conns) for conns in self._connections.values())

    async def broadcast(self, event: str, data: dict = None):
        """
        Broadcast an event to ALL connected clients.
        Dead connections are silently removed.
        """
        message = json.dumps({
            "event": event,
            "data": data or {}
        })

        # Collect all connections first, then send
        dead_connections = []

        async with self._lock:
            all_connections = [
                (user_id, ws)
                for user_id, connections in self._connections.items()
                for ws in connections
            ]

        for user_id, websocket in all_connections:
            try:
                await websocket.send_text(message)
            except Exception:
                # Connection is dead — mark for removal
                dead_connections.append((user_id, websocket))

        # Clean up dead connections
        if dead_connections:
            async with self._lock:
                for user_id, websocket in dead_connections:
                    if user_id in self._connections:
                        self._connections[user_id].discard(websocket)
                        if not self._connections[user_id]:
                            del self._connections[user_id]

        logger.info(f"Broadcast '{event}' to {len(all_connections)} client(s), {len(dead_connections)} dead")

    def broadcast_sync(self, event: str, data: dict = None):
        """
        Synchronous wrapper for broadcasting from sync route handlers.
        
        FastAPI runs sync 'def' handlers in a threadpool — a separate thread
        where there is no running asyncio event loop. We use
        asyncio.run_coroutine_threadsafe() to schedule the broadcast on the
        main uvicorn event loop that was captured during connect().
        
        Safe to call from any thread — will not block or raise.
        """
        if self._loop is None or self._loop.is_closed():
            # No WebSocket has connected yet, or the loop is shut down — skip
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(event, data), self._loop)
        except RuntimeError:
            # Event loop is shut down — skip silently
            pass


# Singleton instance — imported by routes and main.py
manager = ConnectionManager()
