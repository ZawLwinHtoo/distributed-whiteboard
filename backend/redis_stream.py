# Redis Streams manager for durable event sourcing

import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger("room-stream-manager")

class RoomStreamManager:
    """Handles Redis Streams for each whiteboard room."""

    def __init__(self, redis_client):
        self.redis = redis_client  # aioredis client instance

    def _stream_name(self, room_id: str) -> str:
        return f"room:{room_id}:stream"

    def _group_name(self, room_id: str) -> str:
        return f"group:{room_id}"

    async def ensure_group(self, room_id: str) -> None:
        """Create consumer group if it does not exist."""
        stream = self._stream_name(room_id)
        group = self._group_name(room_id)
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
            logger.info(f"Created consumer group {group} for stream {stream}")
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"Consumer group {group} already exists")
            else:
                logger.error(f"Failed to create consumer group {group}: {e}")

    async def append_event(self, room_id: str, event: Dict[str, Any]) -> str:
        """Append a JSON‑encoded event to the room's stream. Returns the message ID."""
        stream = self._stream_name(room_id)
        payload = json.dumps(event)
        msg_id = await self.redis.xadd(stream, {"data": payload})
        logger.debug(f"Appended event to {stream} with id {msg_id}")
        return msg_id

    async def get_history(self, room_id: str, start_id: str = "0-0", end_id: str = "+") -> List[Dict[str, Any]]:
        """Fetch historic events for replay. Returns list of decoded dicts."""
        stream = self._stream_name(room_id)
        raw = await self.redis.xrange(stream, min=start_id, max=end_id)
        history: List[Dict[str, Any]] = []
        for msg_id, fields in raw:
            data = fields.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            try:
                event = json.loads(data)
                history.append(event)
            except Exception as e:
                logger.warning(f"Failed to decode stream entry {msg_id}: {e}")
        logger.info(f"Fetched {len(history)} historic events from {stream}")
        return history

    async def read_stream_batch(
        self,
        room_id: str,
        consumer_name: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Read a batch of new events for a consumer. Uses XREADGROUP with '>' to get unseen messages."""
        stream = self._stream_name(room_id)
        group = self._group_name(room_id)
        await self.ensure_group(room_id)
        try:
            resp = await self.redis.xread_group(group, consumer_name, {stream: ">"}, count=count, block=block_ms)
        except Exception as e:
            logger.error(f"Error reading from stream {stream}: {e}")
            return []

        events: List[Dict[str, Any]] = []
        if not resp:
            return events
        for _stream, msgs in resp:
            for msg_id, fields in msgs:
                data = fields.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    ev = json.loads(data)
                    events.append(ev)
                except Exception as e:
                    logger.warning(f"Failed to decode stream message {msg_id}: {e}")
                try:
                    await self.redis.xack(_stream, group, msg_id)
                except Exception as ack_e:
                    logger.error(f"Failed to ack message {msg_id}: {ack_e}")
        return events
