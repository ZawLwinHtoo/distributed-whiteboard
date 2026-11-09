import pytest
from fastapi.testclient import TestClient
from backend import server


@pytest.fixture
def client(monkeypatch):
    async def memory_only(self):
        self.redis = None
    monkeypatch.setattr(server.ConnectionManager, "init_redis", memory_only)
    monkeypatch.setattr(server, "manager", server.ConnectionManager())
    with TestClient(server.app) as client:
        yield client


def until(socket, kind):
    for _ in range(20):
        message = socket.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"No {kind} message")


def test_history_replay_and_room_isolation(client):
    with client.websocket_connect("/ws/ROOM-A?client_id=host&name=Host") as host:
        assert until(host, "init")["is_host"]
        host.send_json({"type": "draw", "tool": "pen", "points": [{"x": .1, "y": .2}], "color": "#112233"})
        host.send_json({"type": "toggle_ready", "ready": True})
        until(host, "member_list_update")
        with client.websocket_connect("/ws/ROOM-A?client_id=guest&name=Guest") as guest:
            initial = until(guest, "init")
            assert len(initial["history"]) == 1
            assert initial["history"][0]["client_id"] == "host"
            assert not initial["is_host"]
        with client.websocket_connect("/ws/ROOM-B?client_id=other&name=Other") as other:
            assert until(other, "init")["history"] == []


def test_permissions_and_ready_work_without_redis(client):
    with client.websocket_connect("/ws/ROOM-P?client_id=host&name=Host") as host:
        until(host, "init")
        with client.websocket_connect("/ws/ROOM-P?client_id=guest&name=Guest") as guest:
            until(guest, "init")
            guest.send_json({"type": "update_room_permissions", "drawing_locked": True})
            assert "Only the host" in until(guest, "error")["message"]
            host.send_json({"type": "update_room_permissions", "drawing_locked": True})
            changed = until(guest, "permissions_updated")
            assert next(user for user in changed["users"] if user["client_id"] == "guest")["can_draw"] is False
            guest.send_json({"type": "draw", "points": [{"x": .1, "y": .1}]})
            assert "locked" in until(guest, "error")["message"]
            guest.send_json({"type": "toggle_ready", "ready": True})
            members = until(guest, "member_list_update")["members"]
            assert len(members) == 2
            assert next(member for member in members if member["client_id"] == "guest")["is_ready"]


def test_malformed_packets_do_not_close_session(client):
    with client.websocket_connect("/ws/ROOM-M?client_id=host&name=Host") as host:
        until(host, "init")
        host.send_text("{")
        host.send_json([])
        host.send_json({"type": "toggle_ready", "ready": True})
        assert until(host, "member_list_update")["members"][0]["is_ready"]


def test_only_host_starts_session_and_late_join_gets_explicit_rejection(client):
    with client.websocket_connect("/ws/ROOM-S?client_id=host&name=Host") as host:
        until(host, "init")
        with client.websocket_connect("/ws/ROOM-S?client_id=guest&name=Guest") as guest:
            until(guest, "init")
            guest.send_json({"type": "start_session", "mode": "whiteboard"})
            assert "host" in until(guest, "error")["message"]
            host.send_json({"type": "start_session", "mode": "whiteboard"})
            assert until(guest, "start_session")["started_by"] == "host"
            with client.websocket_connect("/ws/ROOM-S?client_id=late&name=Late") as late:
                assert late.receive_json()["type"] == "session_in_progress"


def test_party_game_turns_reveal_and_votes(client):
    with client.websocket_connect("/ws/GAME-1?client_id=host&name=Host") as host:
        until(host, "init")
        with client.websocket_connect("/ws/GAME-1?client_id=guest&name=Guest") as guest:
            until(guest, "init")
            host.send_json({"type":"start_game", "turn_timer":60})
            first = until(host, "turn_start")
            sockets = {"host":host, "guest":guest}
            current = first["active_player"]["client_id"]
            other = guest if current == "host" else host
            other.send_json({"type":"finish_turn"})
            assert "current drawer" in until(other,"error")["message"]
            sockets[current].send_json({"type":"finish_turn"})
            second = until(host,"turn_start")
            assert second["turn_index"] == 1
            sockets[second["active_player"]["client_id"]].send_json({"type":"finish_turn"})
            assert until(host,"game_reveal")["all_layers"] == {"0":[],"1":[]}
            host.send_json({"type":"start_voting"})
            until(host,"voting_start")
            host.send_json({"type":"submit_vote","best_creative":"guest","most_chaotic":"guest"})
            guest.send_json({"type":"submit_vote","best_creative":"host","most_chaotic":"host"})
            results = until(host,"voting_results")
            assert results
