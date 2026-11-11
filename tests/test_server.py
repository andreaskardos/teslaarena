import asyncio

from fastapi.testclient import TestClient

from tesla_arena.server import _broadcast, app, rooms


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


def _receive_until(websocket, predicate, limit: int = 200) -> dict:
    """Read messages until one matches; the room loop may send extra states."""
    for _ in range(limit):
        message = websocket.receive_json()
        if predicate(message):
            return message
    raise AssertionError("Expected message was not received.")


def _is_state(predicate):
    return lambda message: message["type"] == "state" and predicate(
        message["state"]
    )


def _create_started_room(client: TestClient) -> tuple[dict, dict, dict]:
    room = client.post("/api/rooms", json={"private": False}).json()
    host = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Host"}
    ).json()
    guest = client.post(
        f"/api/rooms/{room['room_id']}/join", json={"name": "Guest"}
    ).json()
    return room, host, guest


def test_websocket_sends_state_and_accepts_game_actions() -> None:
    rooms.rooms.clear()
    with TestClient(app) as client:
        room, joined, _guest = _create_started_room(client)
        url = f"{joined['websocket_path']}?token={joined['token']}"

        with client.websocket_connect(url) as websocket:
            initial = websocket.receive_json()
            assert initial["type"] == "state"
            assert initial["state"]["player_limit"] == 12
            assert initial["state"]["players"][0]["coil_capacity"] == 1
            assert initial["state"]["phase"] == "lobby"
            websocket.send_json({"type": "place_coil"})
            assert _receive_until(
                websocket, lambda message: message["type"] == "error"
            ) == {
                "type": "error",
                "message": "The room creator has not started the match.",
            }
            assert client.post(
                f"/api/rooms/{room['room_id']}/start",
                headers={"X-Player-Token": joined["token"]},
            ).json() == {"phase": "playing"}
            _receive_until(
                websocket, _is_state(lambda state: state["phase"] == "playing")
            )
            websocket.send_json({"type": "place_coil"})
            _receive_until(websocket, _is_state(lambda state: len(state["coils"]) == 1))

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
            assert _receive_until(
                websocket, lambda message: message["type"] == "error"
            ) == {
                "type": "error",
                "message": "Spectators cannot control the match.",
            }


def test_room_loop_advances_match_without_client_messages() -> None:
    rooms.rooms.clear()
    with TestClient(app) as client:
        room, host, guest = _create_started_room(client)
        client.post(
            f"/api/rooms/{room['room_id']}/start",
            headers={"X-Player-Token": host["token"]},
        )
        host_url = f"{host['websocket_path']}?token={host['token']}"
        guest_url = f"{guest['websocket_path']}?token={guest['token']}"
        with client.websocket_connect(host_url) as host_ws, client.websocket_connect(
            guest_url
        ) as guest_ws:
            game_room = rooms.rooms[room["room_id"]]
            assert game_room.loop_task is not None
            host_ws.send_json({"type": "place_coil"})
            _receive_until(host_ws, _is_state(lambda state: len(state["coils"]) == 1))
            # Expire the fuse; only the server loop can notice and explode it.
            for coil in game_room.game.coils.values():
                coil.explodes_at = 0
            exploded = _is_state(
                lambda state: not state["coils"] and state["lightning"]
            )
            _receive_until(host_ws, exploded)
            _receive_until(guest_ws, exploded)

        assert game_room.connections == {}
        assert game_room.loop_task is None


class _FakeConnection:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        if self.fail:
            raise RuntimeError("Cannot send once the socket is closed.")
        self.sent.append(message)


def test_broadcast_skips_unchanged_state_and_drops_dead_connections() -> None:
    rooms.rooms.clear()
    room, _invite = rooms.create_room(False)
    healthy, dead = _FakeConnection(), _FakeConnection(fail=True)
    room.connections = {"player-1": healthy, "player-2": dead}

    asyncio.run(_broadcast(room))
    asyncio.run(_broadcast(room))

    assert len(healthy.sent) == 1
    assert room.connections == {"player-1": healthy}


def test_only_room_creator_can_start_the_match() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room, host, guest = _create_started_room(client)

    unauthorized = client.post(
        f"/api/rooms/{room['room_id']}/start",
        headers={"X-Player-Token": guest["token"]},
    )
    assert unauthorized.status_code == 403
    started = client.post(
        f"/api/rooms/{room['room_id']}/start",
        headers={"X-Player-Token": host["token"]},
    )
    assert started.json() == {"phase": "playing"}
    assert rooms.rooms[room["room_id"]].bots == {}


def test_lone_creator_starts_against_bots_that_play() -> None:
    rooms.rooms.clear()
    with TestClient(app) as client:
        room = client.post("/api/rooms", json={"private": False}).json()
        host = client.post(
            f"/api/rooms/{room['room_id']}/join", json={"name": "Solo"}
        ).json()
        url = f"{host['websocket_path']}?token={host['token']}"
        with client.websocket_connect(url) as websocket:
            websocket.receive_json()
            started = client.post(
                f"/api/rooms/{room['room_id']}/start",
                headers={"X-Player-Token": host["token"]},
            )
            assert started.json() == {"phase": "playing"}
            playing = _receive_until(
                websocket, _is_state(lambda state: state["phase"] == "playing")
            )
            bots = [p for p in playing["state"]["players"] if p["bot_skill"]]
            assert [bot["bot_skill"] for bot in bots] == ["easy", "medium", "hard"]
            spawns = {bot["id"]: (bot["x"], bot["y"]) for bot in bots}

            # The room loop drives the bots without any client input.
            _receive_until(
                websocket,
                _is_state(
                    lambda state: any(
                        (p["x"], p["y"]) != spawns[p["id"]]
                        for p in state["players"]
                        if p["id"] in spawns
                    )
                ),
            )


def test_creator_manages_bots_over_http() -> None:
    rooms.rooms.clear()
    client = TestClient(app)
    room = client.post("/api/rooms", json={"private": False}).json()
    room_id = room["room_id"]
    host = client.post(f"/api/rooms/{room_id}/join", json={"name": "Host"}).json()
    guest = client.post(f"/api/rooms/{room_id}/join", json={"name": "Guest"}).json()

    def add(token: str, skill: str = "medium"):
        return client.post(
            f"/api/rooms/{room_id}/bots",
            json={"skill": skill},
            headers={"X-Player-Token": token},
        )

    assert add(guest["token"]).status_code == 403
    assert add(host["token"], "genius").status_code == 422
    added = add(host["token"], "hard")
    assert added.status_code == 200
    assert added.json()["name"] == "Volt (svår)"
    bot_id = added.json()["bot_id"]
    assert rooms.rooms[room_id].game.players[bot_id].bot_skill == "hard"

    removed = client.delete(
        f"/api/rooms/{room_id}/bots/{bot_id}",
        headers={"X-Player-Token": host["token"]},
    )
    assert removed.json() == {"removed": bot_id}
    missing = client.delete(
        f"/api/rooms/{room_id}/bots/{bot_id}",
        headers={"X-Player-Token": host["token"]},
    )
    assert missing.status_code == 404

    add(host["token"], "easy")
    client.post(
        f"/api/rooms/{room_id}/start", headers={"X-Player-Token": host["token"]}
    )
    assert add(host["token"]).status_code == 409
    assert len(rooms.rooms[room_id].game.players) == 3
