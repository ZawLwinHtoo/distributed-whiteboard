import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class RoomManager:
    """Handles room meta and member state stored in Redis.

    Uses the existing ConnectionManager's Redis client for storage.
    """

    def __init__(self, redis_client):
        self.redis = redis_client
        self.memory_meta = {}
        self.memory_members = {}

    # ---------- Meta ----------
    async def get_meta(self, room_id: str) -> Dict[str, str]:
        key = f"room:{room_id}:meta"
        if not self.redis:
            return dict(self.memory_meta.get(room_id, {}))
        try:
            raw = await self.redis.hgetall(key)
            # Decode bytes if needed
            return {k.decode() if isinstance(k, bytes) else str(k): v.decode() if isinstance(v, bytes) else str(v) for k, v in raw.items()}
        except Exception as e:
            logger.error(f"Error fetching room meta for {room_id}: {e}")
            return {}

    async def set_meta_field(self, room_id: str, field: str, value: str) -> None:
        key = f"room:{room_id}:meta"
        if not self.redis:
            self.memory_meta.setdefault(room_id, {})[field] = value
            return
        try:
            await self.redis.hset(key, field, value)
        except Exception as e:
            logger.error(f"Error setting meta {field} for {room_id}: {e}")

    # ---------- Members ----------
    async def add_member(self, room_id: str, member: Dict) -> None:
        """Add or update a member in the Redis hash.
        member must contain at least 'client_id'.
        """
        if not self.redis:
            self.memory_members.setdefault(room_id, {})[member["client_id"]] = dict(member)
            return
        key = f"users:{room_id}"
        client_id = member.get("client_id")
        if not client_id:
            return
        try:
            await self.redis.hset(key, client_id, json.dumps(member))
        except Exception as e:
            logger.error(f"Error adding member {client_id} to {room_id}: {e}")

    async def remove_member(self, room_id: str, client_id: str) -> None:
        if not self.redis:
            self.memory_members.get(room_id, {}).pop(client_id, None)
            return
        key = f"users:{room_id}"
        try:
            await self.redis.hdel(key, client_id)
        except Exception as e:
            logger.error(f"Error removing member {client_id} from {room_id}: {e}")

    async def get_members(self, room_id: str) -> List[Dict]:
        key = f"users:{room_id}"
        if not self.redis:
            return [dict(member) for member in self.memory_members.get(room_id, {}).values()]
        try:
            raw = await self.redis.hgetall(key)
            members = []
            for cid, val in raw.items():
                cid_str = cid.decode() if isinstance(cid, bytes) else str(cid)
                val_str = val.decode() if isinstance(val, bytes) else str(val)
                try:
                    members.append(json.loads(val_str))
                except Exception:
                    logger.warning(f"Malformed member payload for {cid_str} in {room_id}")
            return members
        except Exception as e:
            logger.error(f"Error fetching members for {room_id}: {e}")
            return []

    async def set_ready(self, room_id: str, client_id: str, ready: bool) -> None:
        members = await self.get_members(room_id)
        for m in members:
            if m.get("client_id") == client_id:
                m["is_ready"] = ready
                await self.add_member(room_id, m)
                break

    async def all_ready(self, room_id: str) -> bool:
        members = await self.get_members(room_id)
        if not members:
            return False
        return all(m.get("is_ready") for m in members)

    # ---------- State Transitions ----------
    async def set_status(self, room_id: str, status: str) -> None:
        await self.set_meta_field(room_id, "status", status)

    async def get_status(self, room_id: str) -> Optional[str]:
        meta = await self.get_meta(room_id)
        return meta.get("status")

    # Convenience for lobby flow
    async def maybe_start_game(self, room_id: str) -> bool:
        """If all members are ready, transition to COUNTDOWN and return True.
        Host is expected to trigger start_game; this helper can be used by host.
        """
        if await self.all_ready(room_id):
            await self.set_status(room_id, "countdown")
            return True
        return False
