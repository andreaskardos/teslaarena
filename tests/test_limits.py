import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tesla_arena import rooms as rooms_module
from tesla_arena.game import GameError
from tesla_arena.limits import TokenBucket, WindowLimiter
from tesla_arena.rooms import RoomLimitError, RoomManager
from tesla_arena.server import ACTION_BURST, app, rooms


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_window_limiter_allows_a_limited_number_per_key_and_window() -> None:
    clock = FakeClock()
    limiter = WindowLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b"), "keys are limited separately"
    clock.now += 60
    assert limiter.allow("a")


def test_token_bucket_allows_bursts_and_refills_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=2, capacity=3, clock=clock)

    assert [bucket.allow() for _ in range(4)] == [True, True, True, False]
    clock.now += 0.5
    assert bucket.allow()
    assert not bucket.allow()
    clock.now += 100
    assert sum(bucket.allow() for _ in range(10)) == 3, "never above capacity"


def test_room_creation_is_limited_per_client() -> None:
    rooms.rooms.clear()
    client = TestClient(app)

    statuses = [
        client.post("/api/rooms", json={"private": False}).status_code
        for _ in range(6)
    ]

    assert statuses == [200] * 5 + [429]


def test_joins_are_limited_per_client() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room_id = client.post(
        "/api/rooms", json={"private": True}
    ).json()["room_id"]

    statuses = {
        client.post(
            f"/api/rooms/{room_id}/join", json={"name": "X", "invite_code": "bad"}
        ).status_code
        for _ in range(31)
    }

    assert statuses == {403, 429}


def test_server_wide_room_and_spectator_caps(monkeypatch) -> None:
    monkeypatch.setattr(rooms_module, "MAX_ROOMS", 1)
    monkeypatch.setattr(rooms_module, "MAX_SPECTATORS_PER_ROOM", 1)
    manager = RoomManager()
    room, _ = manager.create_room(private=False)
    with pytest.raises(RoomLimitError):
        manager.create_room(private=False)

    _, host_id, token, _ = manager.join_room(room.id, "Host", None)
    manager.start_room(room.id, host_id, token)
    manager.join_room(room.id, "Viewer", None)
    with pytest.raises(GameError, match="spectator places"):
        manager.join_room(room.id, "Another", None)


def test_flooding_actions_are_dropped_with_a_warning() -> None:
    rooms.rooms.clear()
    with TestClient(app) as client:
        room_id = client.post("/api/rooms", json={"private": False}).json()["room_id"]
        host = client.post(f"/api/rooms/{room_id}/join", json={"name": "Host"}).json()
        url = f"{host['websocket_path']}?token={host['token']}"
        with client.websocket_connect(url) as websocket:
            websocket.receive_json()
            for _ in range(ACTION_BURST + 20):
                websocket.send_json({"type": "place_coil"})
            errors = set()
            while "Too many actions. Slow down." not in errors:
                message = websocket.receive_json()
                if message["type"] == "error":
                    errors.add(message["message"])
            # The connection stays usable once the bucket has refilled a little.
            time.sleep(0.2)
            websocket.send_json({"type": "bogus"})
            assert websocket.receive_json()["type"] == "error"


def test_oversized_messages_close_the_connection() -> None:
    rooms.rooms.clear()
    with TestClient(app) as client:
        room_id = client.post("/api/rooms", json={"private": False}).json()["room_id"]
        host = client.post(f"/api/rooms/{room_id}/join", json={"name": "Host"}).json()
        url = f"{host['websocket_path']}?token={host['token']}"
        with client.websocket_connect(url) as websocket:
            websocket.receive_json()
            websocket.send_text("x" * 5000)
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    websocket.receive_json()
            assert closed.value.code == 1009
