import pytest
from fastapi.testclient import TestClient

from tesla_arena.game import GameError, GameState
from tesla_arena.rooms import (
    DISCONNECT_GRACE_SECONDS,
    EMPTY_ROOM_SECONDS,
    Room,
    RoomManager,
)
from tesla_arena.server import app, rooms

CONNECTED = object()  # Stands in for a WebSocket in room-level tests.


def _room_with(manager: RoomManager, *names: str, player_limit: int = 12):
    room, _ = manager.create_room(private=False, player_limit=player_limit)
    start = room.empty_since
    members = []
    for name in names:
        _, member_id, token, _ = manager.join_room(room.id, name, None, now=start)
        connect(manager, room, member_id)
        members.append((member_id, token))
    return room, start, members


def connect(manager: RoomManager, room: Room, member_id: str) -> None:
    room.connections[member_id] = CONNECTED
    manager.member_connected(room, member_id)


def disconnect(manager: RoomManager, room: Room, member_id: str, now: float) -> None:
    del room.connections[member_id]
    manager.member_disconnected(room, member_id, now)


def test_join_without_connecting_frees_the_slot_after_the_grace_period() -> None:
    manager = RoomManager()
    room, _ = manager.create_room(private=False)
    start = room.empty_since
    manager.join_room(room.id, "Ghost", None, now=start)

    assert manager.sweep(start + DISCONNECT_GRACE_SECONDS - 1) == []
    assert len(room.game.players) == 1

    assert manager.sweep(start + DISCONNECT_GRACE_SECONDS) == [room]
    assert room.game.players == {}
    manager.sweep(start + EMPTY_ROOM_SECONDS)
    assert room.id not in manager.rooms, "a room nobody is in is deleted"


def test_reconnecting_within_the_grace_period_keeps_the_player() -> None:
    manager = RoomManager()
    room, start, [(host_id, _), _] = _room_with(manager, "Host", "Guest")

    disconnect(manager, room, host_id, start + 1)
    manager.sweep(start + DISCONNECT_GRACE_SECONDS)
    connect(manager, room, host_id)
    manager.sweep(start + 100)

    assert host_id in room.game.players
    assert room.creator_id == host_id


def test_creator_who_stays_away_in_the_lobby_hands_over_the_room() -> None:
    manager = RoomManager()
    room, start, [(host_id, _), (guest_id, guest_token)] = _room_with(
        manager, "Host", "Guest"
    )

    disconnect(manager, room, host_id, start + 1)
    changed = manager.sweep(start + 1 + DISCONNECT_GRACE_SECONDS)

    assert changed == [room]
    assert host_id not in room.game.players
    assert room.creator_id == guest_id
    manager.add_bot(room.id, guest_token, "easy")  # the guest now has creator rights


def test_player_who_drops_mid_match_is_eliminated_and_marked_as_left() -> None:
    manager = RoomManager()
    room, start, [(host_id, host_token), (guest_id, _), (third_id, _)] = _room_with(
        manager, "Host", "Guest", "Third"
    )
    manager.start_room(room.id, host_id, host_token)

    disconnect(manager, room, guest_id, start + 1)
    manager.sweep(start + 1 + DISCONNECT_GRACE_SECONDS)

    guest = room.game.players[guest_id]
    assert not guest.alive and guest.left
    assert room.phase == "playing"
    manager.leave(room, third_id)
    assert room.phase == "finished"
    assert room.game.snapshot()["winner_id"] == host_id


def test_leaving_on_purpose_takes_effect_at_once() -> None:
    manager = RoomManager()
    room, _, [(host_id, _), (guest_id, _)] = _room_with(manager, "Host", "Guest")

    manager.leave(room, guest_id)

    assert guest_id not in room.game.players
    assert guest_id not in room.player_tokens
    assert room.creator_id == host_id


def test_room_is_deleted_only_after_it_has_been_empty_for_a_while() -> None:
    manager = RoomManager()
    room, start, [(host_id, _)] = _room_with(manager, "Host")
    manager.leave(room, host_id)
    del room.connections[host_id]
    manager.member_disconnected(room, host_id, start + 5)

    manager.sweep(start + 5 + EMPTY_ROOM_SECONDS - 1)
    assert room.id in manager.rooms
    manager.sweep(start + 5 + EMPTY_ROOM_SECONDS)
    assert room.id not in manager.rooms


def test_rematch_returns_a_finished_room_to_its_lobby() -> None:
    manager = RoomManager()
    room, _, [(host_id, host_token), (guest_id, guest_token), (gone_id, _)] = (
        _room_with(manager, "Host", "Guest", "Gone", player_limit=6)
    )
    bot = manager.add_bot(room.id, host_token, "hard")
    manager.start_room(room.id, host_id, host_token)
    old_arena = room.game

    with pytest.raises(GameError, match="not over"):
        manager.rematch(room.id, host_token)
    manager.leave(room, gone_id)
    room.game.players[bot.id].lives = 0
    room.game.players[guest_id].lives = 0
    with pytest.raises(PermissionError, match="room creator"):
        manager.rematch(room.id, guest_token)

    manager.rematch(room.id, host_token)

    assert room.phase == "lobby"
    assert room.game is not old_arena
    assert list(room.game.players) == [host_id, guest_id, bot.id]
    assert all(player.alive for player in room.game.players.values())
    assert room.game.players[bot.id].bot_skill == "hard"
    assert not room.game.coils and not room.game.powerups
    # The next joiner gets a fresh id rather than reusing one from the old match.
    _, late_id, _, _ = manager.join_room(room.id, "Late", None)
    assert late_id not in {host_id, guest_id, gone_id, bot.id}


def test_rematch_arena_keeps_the_room_settings() -> None:
    game = GameState.create(
        width=25, height=25, player_limit=4, soft_block_percentage=40
    )
    game.join_player("Host")

    fresh = game.rematch()

    assert (fresh.width, fresh.height, fresh.player_limit) == (25, 25, 4)
    assert fresh.soft_block_percentage == 40
    assert len(fresh.soft_blocks) == len(game.soft_blocks)


def test_websocket_leave_and_reconnect() -> None:
    rooms.rooms.clear()
    with TestClient(app) as client:
        room_id = client.post("/api/rooms", json={"private": False}).json()["room_id"]
        host = client.post(f"/api/rooms/{room_id}/join", json={"name": "Host"}).json()
        guest = client.post(f"/api/rooms/{room_id}/join", json={"name": "Guest"}).json()
        host_url = f"{host['websocket_path']}?token={host['token']}"
        guest_url = f"{guest['websocket_path']}?token={guest['token']}"
        room = rooms.rooms[room_id]

        with client.websocket_connect(host_url) as host_ws:
            host_ws.receive_json()
            # Dropping without leaving keeps the place for a reconnect.
            with client.websocket_connect(guest_url) as guest_ws:
                guest_ws.receive_json()
            assert guest["member_id"] in room.disconnected_at
            with client.websocket_connect(guest_url) as guest_ws:
                guest_ws.receive_json()
                assert guest["member_id"] not in room.disconnected_at
                guest_ws.send_json({"type": "leave"})

            state = host_ws.receive_json()["state"]
            while len(state["players"]) != 1:
                state = host_ws.receive_json()["state"]
            assert [p["name"] for p in state["players"]] == ["Host"]
            assert guest["member_id"] not in room.player_tokens


def test_rematch_over_http() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room_id = client.post("/api/rooms", json={"private": False}).json()["room_id"]
    host = client.post(f"/api/rooms/{room_id}/join", json={"name": "Host"}).json()
    client.post(f"/api/rooms/{room_id}/join", json={"name": "Guest"})
    headers = {"X-Player-Token": host["token"]}

    client.post(f"/api/rooms/{room_id}/start", headers=headers)
    assert client.post(f"/api/rooms/{room_id}/rematch", headers=headers).status_code == 409
    for player in rooms.rooms[room_id].game.players.values():
        player.lives = 0

    response = client.post(f"/api/rooms/{room_id}/rematch", headers=headers)

    assert response.json() == {"phase": "lobby"}
    assert client.post(f"/api/rooms/{room_id}/start", headers=headers).json() == {
        "phase": "playing"
    }
