from fastapi.testclient import TestClient

from tesla_arena.server import app, rooms


def test_lobby_and_private_room_http_flow() -> None:
    rooms.rooms.clear()
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/game.js").status_code == 200

    created = client.post("/api/rooms", json={"private": True})
    assert created.status_code == 200
    private_room = created.json()
    assert private_room["max_players"] == 12
    assert private_room["soft_block_count"] == 16
    assert private_room["arena_size"] == 35
    assert private_room["room_id"] not in {
        room["id"] for room in client.get("/api/rooms").json()
    }

    denied = client.post(
        f"/api/rooms/{private_room['room_id']}/join",
        json={"name": "Guest", "invite_code": "wrong"},
    )
    assert denied.status_code == 403

    joined = client.post(
        f"/api/rooms/{private_room['room_id']}/join",
        json={"name": "Guest", "invite_code": private_room["invite_code"]},
    )
    assert joined.status_code == 200
    assert joined.json()["member_id"] == "player-1"
    assert joined.json()["role"] == "creator"


def test_room_creation_accepts_custom_soft_block_count() -> None:
    rooms.rooms.clear()
    client = TestClient(app)

    created = client.post(
        "/api/rooms",
        json={"private": False, "soft_block_count": 75},
    )

    assert created.status_code == 200
    room_id = created.json()["room_id"]
    assert created.json()["soft_block_count"] == 75
    assert len(rooms.rooms[room_id].game.soft_blocks) == 75
    assert rooms.rooms[room_id].game.snapshot()["soft_blocks_remaining"] == 75


def test_room_creation_accepts_size_player_count_and_block_percentage() -> None:
    rooms.rooms.clear()
    client = TestClient(app)

    created = client.post(
        "/api/rooms",
        json={
            "private": False,
            "arena_size": 45,
            "player_limit": 8,
            "soft_block_percentage": 25,
        },
    )

    assert created.status_code == 200
    room_id = created.json()["room_id"]
    room = rooms.rooms[room_id]
    assert created.json()["arena_size"] == 45
    assert created.json()["max_players"] == 8
    assert created.json()["soft_block_percentage"] == 25
    assert room.game.width == room.game.height == 45
    assert room.game.player_limit == 8
    assert len(room.game.soft_blocks) == int(
        sum(
            (x, y) not in room.game.hard_walls
            and all(
                abs(x - spawn_x) + abs(y - spawn_y) > 2
                for spawn_x, spawn_y in room.game.spawn_points()
            )
            for y in range(2, 43)
            for x in range(2, 43)
        )
        * 0.25
        + 0.5
    )


def test_room_creation_rejects_soft_block_count_above_limit() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/rooms",
        json={"private": False, "soft_block_count": 201},
    )

    assert response.status_code == 422


def test_room_creation_rejects_unsupported_arena_size() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/rooms",
        json={"private": False, "arena_size": 30},
    )

    assert response.status_code == 422


def test_websocket_sends_state_and_accepts_game_actions() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room = client.post("/api/rooms", json={"private": False}).json()
    joined = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Player"}
    ).json()
    guest = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Guest"}
    ).json()
    url = f"{joined['websocket_path']}?token={joined['token']}"

    with client.websocket_connect(url) as websocket:
        initial = websocket.receive_json()
        assert initial["type"] == "state"
        assert initial["state"]["player_limit"] == 12
        assert initial["state"]["players"][0]["coil_capacity"] == 1
        assert initial["state"]["phase"] == "lobby"
        websocket.send_json({"type": "place_coil"})
        rejected = websocket.receive_json()
        assert rejected == {
            "type": "error",
            "message": "The room creator has not started the match.",
        }
        websocket.receive_json()
        assert client.post(
            f"/api/rooms/{room['room_id']}/start",
            headers={"X-Player-Token": joined["token"]},
        ).json() == {"phase": "playing"}
        updated = websocket.receive_json()
        assert updated["state"]["phase"] == "playing"
        websocket.send_json({"type": "place_coil"})
        updated = websocket.receive_json()
        assert updated["type"] == "state"
        assert len(updated["state"]["coils"]) == 1

    late_join = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Viewer"}
    ).json()
    assert late_join["role"] == "spectator"
    assert late_join["member_id"].startswith("spectator-")
    spectator_url = f"{late_join['websocket_path']}?token={late_join['token']}"
    with client.websocket_connect(spectator_url) as websocket:
        initial = websocket.receive_json()
        assert initial["state"]["spectator_count"] == 1
        websocket.send_json({"type": "place_coil"})
        rejected = websocket.receive_json()
        assert rejected == {
            "type": "error",
            "message": "Spectators cannot control the match.",
        }
        websocket.receive_json()


def test_only_room_creator_can_start_and_two_players_are_required() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room = client.post("/api/rooms", json={"private": False}).json()
    host = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Host"}
    ).json()

    too_early = client.post(
        f"/api/rooms/{room['room_id']}/start",
        headers={"X-Player-Token": host["token"]},
    )
    assert too_early.status_code == 409

    guest = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Guest"}
    ).json()
    unauthorized = client.post(
        f"/api/rooms/{room['room_id']}/start",
        headers={"X-Player-Token": guest["token"]},
    )
    assert unauthorized.status_code == 403
