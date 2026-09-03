"""Blind Chain Relay game mode utilities.

This module provides helper functions for the Blind-Chain Relay game mode,
including slice assignment for each player and visibility checks for strokes.
"""

import time
from typing import List, Dict, Tuple, Optional


class BlindChainGame:
    """Utility class for the Blind-Chain Relay game mode.

    The game partitions the canvas into N horizontal slices, one per player.
    Each player can draw only within their own slice plus a small seam (default 20px)
    that overlaps adjacent slices to allow continuity.
    """

    SEAM_PX: int = 20  # pixel overlap between slices; front‑end will use the same value.
    DEFAULT_TIMER: int = 60  # default round duration in seconds

    @staticmethod
    def assign_slices(players: List[Dict]) -> Dict[str, Tuple[float, float]]:
        """Assign a vertical slice (as a 0‑1 normalized range) to each player.

        Args:
            players: List of player dicts containing at least a ``client_id`` key.
        Returns:
            Mapping of ``client_id`` → (slice_start, slice_end) where the values are
            normalized percentages of the canvas height.
        """
        count = len(players)
        if count == 0:
            return {}
        slice_height = 1.0 / count
        slices: Dict[str, Tuple[float, float]] = {}
        for idx, player in enumerate(players):
            start = idx * slice_height
            end = (idx + 1) * slice_height
            slices[player["client_id"]] = (start, end)
        return slices

    @staticmethod
    def is_visible(y: float, slice_range: Tuple[float, float], canvas_height_px: int) -> bool:
        """Determine whether a stroke at vertical coordinate ``y`` (normalized 0‑1) should
        be visible to the player owning ``slice_range``.

        The seam is defined as ``SEAM_PX`` pixels; we convert it to a normalized value
        based on the supplied ``canvas_height_px``.
        """
        start, end = slice_range
        seam_norm = BlindChainGame.SEAM_PX / max(canvas_height_px, 1)
        # Allow visibility if inside the slice or within the seam overlapping neighbour slices.
        return y >= start - seam_norm and y <= end + seam_norm

    @staticmethod
    def start_session(
        room_id: str,
        players: List[Dict],
        host_client_id: str,
        timer_seconds: int = 60,
        canvas_height_px: int = 800,
    ) -> Dict:
        """Build the initial Blind-Chain Relay session dict.

        Args:
            room_id: The room code.
            players: List of player dicts (must have ``client_id``, ``name``, ``color``).
            host_client_id: The client_id of the host who started the session.
            timer_seconds: Round duration in seconds.
            canvas_height_px: Reference canvas height for seam calculation.

        Returns:
            A session dict ready to be stored in ``game_sessions``.
        """
        slices = BlindChainGame.assign_slices(players)
        return {
            "room_code": room_id,
            "status": "blind_chain_playing",
            "mode": "blind_chain",
            "host_client_id": host_client_id,
            "players": players,
            "slices": {cid: {"start": s, "end": e} for cid, (s, e) in slices.items()},
            "all_strokes": [],
            "timer_seconds": timer_seconds,
            "canvas_height_px": canvas_height_px,
            "started_at": time.time(),
            "timer_task": None,
        }

    @staticmethod
    def should_relay_stroke(
        stroke: Dict,
        recipient_slice: Tuple[float, float],
        canvas_height_px: int,
    ) -> bool:
        """Determine if a stroke action should be relayed to a recipient based on
        whether any part of the stroke falls within the recipient's visible band.

        Handles all tool types: pen, eraser (points[]), line (startX/Y, endX/Y),
        rect, circle (bounding box check).

        Args:
            stroke: The draw action dict.
            recipient_slice: (start, end) normalized range for the recipient.
            canvas_height_px: Pixel height of the canvas for seam calculation.

        Returns:
            True if the stroke should be visible to the recipient.
        """
        tool = stroke.get("tool", "pen")
        seam_norm = BlindChainGame.SEAM_PX / max(canvas_height_px, 1)
        vis_start = recipient_slice[0] - seam_norm
        vis_end = recipient_slice[1] + seam_norm

        def y_in_range(y_val: float) -> bool:
            return vis_start <= y_val <= vis_end

        if tool in ("pen", "eraser"):
            points = stroke.get("points", [])
            for pt in points:
                if y_in_range(pt.get("y", 0)):
                    return True
            return False

        elif tool == "line":
            sy = stroke.get("startY", 0)
            ey = stroke.get("endY", 0)
            mid_y = (sy + ey) / 2.0
            return y_in_range(sy) or y_in_range(ey) or y_in_range(mid_y)

        elif tool == "rect":
            sy = stroke.get("startY", 0)
            ey = stroke.get("endY", 0)
            rect_top = min(sy, ey)
            rect_bottom = max(sy, ey)
            # Rectangle intersects the visible band if they overlap
            return rect_top <= vis_end and rect_bottom >= vis_start

        elif tool == "circle":
            cx = stroke.get("startX", 0)
            cy = stroke.get("startY", 0)
            ex = stroke.get("endX", 0)
            ey = stroke.get("endY", 0)
            radius_norm = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            circle_top = cy - radius_norm
            circle_bottom = cy + radius_norm
            return circle_top <= vis_end and circle_bottom >= vis_start

        # Unknown tool — relay by default
        return True

    @staticmethod
    def is_stroke_in_sender_slice(
        stroke: Dict,
        sender_slice: Tuple[float, float],
        canvas_height_px: int,
    ) -> bool:
        """Validate that ALL points of a stroke fall within the sender's allowed drawing zone
        (their slice + seam).

        This is a server-side enforcement check to prevent cheating.
        """
        seam_norm = BlindChainGame.SEAM_PX / max(canvas_height_px, 1)
        allowed_start = sender_slice[0] - seam_norm
        allowed_end = sender_slice[1] + seam_norm
        tool = stroke.get("tool", "pen")

        def y_in_range(y_val: float) -> bool:
            return allowed_start <= y_val <= allowed_end

        if tool in ("pen", "eraser"):
            points = stroke.get("points", [])
            return all(y_in_range(pt.get("y", 0)) for pt in points)

        elif tool == "line":
            return y_in_range(stroke.get("startY", 0)) and y_in_range(stroke.get("endY", 0))

        elif tool == "rect":
            return y_in_range(stroke.get("startY", 0)) and y_in_range(stroke.get("endY", 0))

        elif tool == "circle":
            cy = stroke.get("startY", 0)
            ex = stroke.get("endX", 0)
            ey = stroke.get("endY", 0)
            cx = stroke.get("startX", 0)
            radius_norm = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            return y_in_range(cy - radius_norm) and y_in_range(cy + radius_norm)

        return True

    @staticmethod
    def get_all_strokes(session: Dict) -> List[Dict]:
        """Return the complete flat list of buffered strokes for reveal."""
        return session.get("all_strokes", [])
