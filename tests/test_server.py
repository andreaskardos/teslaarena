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
    assert joined.json()["player_id"] == "player-1"


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


def test_room_creation_rejects_soft_block_count_above_limit() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/rooms",
        json={"private": False, "soft_block_count": 201},
    )

    assert response.status_code == 422


def test_websocket_sends_state_and_accepts_game_actions() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room = client.post("/api/rooms", json={"private": False}).json()
    joined = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Player"}
    ).json()
    url = f"{joined['websocket_path']}?token={joined['token']}"

    with client.websocket_connect(url) as websocket:
        initial = websocket.receive_json()
        assert initial["type"] == "state"
        assert initial["state"]["player_limit"] == 12
        assert initial["state"]["players"][0]["coil_capacity"] == 1
        websocket.send_json({"type": "place_coil"})
        updated = websocket.receive_json()
        assert updated["type"] == "state"
        assert len(updated["state"]["coils"]) == 1
