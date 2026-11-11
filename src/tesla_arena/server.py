from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tesla_arena.game import (
    DEFAULT_ARENA_SIZE,
    DEFAULT_SOFT_BLOCK_PERCENTAGE,
    MAX_PLAYERS,
    GameError,
)
from tesla_arena.rooms import Room, RoomManager


class CreateRoomRequest(BaseModel):
    private: bool = False
    arena_size: Literal[25, 35, 45] = DEFAULT_ARENA_SIZE
    player_limit: int = Field(default=MAX_PLAYERS, ge=2, le=MAX_PLAYERS)
    soft_block_percentage: float = Field(
        default=DEFAULT_SOFT_BLOCK_PERCENTAGE, ge=0, le=100
    )
    soft_block_count: int | None = Field(default=None, ge=0, le=200)


class JoinRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    invite_code: str | None = None


class AddBotRequest(BaseModel):
    skill: Literal["easy", "medium", "hard"] = "medium"


class GameAction(BaseModel):
    type: Literal["move", "place_coil", "detonate_remote"]
    direction: Literal["up", "down", "left", "right"] | None = None


app = FastAPI(title="Tesla Arena")
rooms = RoomManager()
WEB_DIRECTORY = Path(__file__).resolve().parents[2] / "web"
DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
TICK_SECONDS = 0.05


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "index.html")


@app.get("/api/rooms")
async def list_rooms() -> list[dict[str, object]]:
    return rooms.public_rooms()


@app.post("/api/rooms")
async def create_room(request: CreateRoomRequest) -> dict[str, object]:
    try:
        room, invite_code = rooms.create_room(
            request.private,
            request.soft_block_count,
            arena_size=request.arena_size,
            player_limit=request.player_limit,
            soft_block_percentage=request.soft_block_percentage,
        )
    except GameError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result: dict[str, object] = {
        "room_id": room.id,
        "max_players": room.game.player_limit,
        "arena_size": room.game.width,
        "soft_block_count": room.game.soft_block_count,
        "soft_block_percentage": room.game.soft_block_percentage,
    }
    if invite_code is not None:
        result["invite_code"] = invite_code
    return result


@app.post("/api/rooms/{room_id}/join")
async def join_room(room_id: str, request: JoinRoomRequest) -> dict[str, str]:
    try:
        room, member_id, token, role = rooms.join_room(
            room_id, request.name, request.invite_code
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except GameError as error:
        status = 404 if "not found" in str(error).lower() else 409
        raise HTTPException(status_code=status, detail=str(error)) from error
    return {
        "room_id": room.id,
        "member_id": member_id,
        "role": role,
        "token": token,
        "websocket_path": f"/ws/{room.id}/{member_id}",
    }


@app.post("/api/rooms/{room_id}/start")
async def start_room(
    room_id: str, player_token: str = Header(alias="X-Player-Token")
) -> dict[str, str]:
    room_id = room_id.upper()
    room = rooms.rooms.get(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found.")
    creator_id = room.creator_id
    if creator_id is None:
        raise HTTPException(status_code=409, detail="The room creator has not joined.")
    try:
        rooms.start_room(room_id, creator_id, player_token)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except GameError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await _broadcast(room)
    return {"phase": room.phase}


@app.post("/api/rooms/{room_id}/bots")
async def add_bot(
    room_id: str,
    request: AddBotRequest,
    player_token: str = Header(alias="X-Player-Token"),
) -> dict[str, str]:
    with _creator_errors():
        bot = rooms.add_bot(room_id, player_token, request.skill)
    await _broadcast(rooms.rooms[room_id.upper()])
    return {"bot_id": bot.id, "name": bot.name, "skill": request.skill}


@app.delete("/api/rooms/{room_id}/bots/{bot_id}")
async def remove_bot(
    room_id: str,
    bot_id: str,
    player_token: str = Header(alias="X-Player-Token"),
) -> dict[str, str]:
    with _creator_errors():
        rooms.remove_bot(room_id, player_token, bot_id)
    await _broadcast(rooms.rooms[room_id.upper()])
    return {"removed": bot_id}


@contextmanager
def _creator_errors() -> Iterator[None]:
    """Translate room-manager errors for creator-only lobby actions to HTTP."""
    try:
        yield
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except GameError as error:
        status = 404 if "not found" in str(error).lower() else 409
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.websocket("/ws/{room_id}/{player_id}")
async def game_socket(websocket: WebSocket, room_id: str, player_id: str) -> None:
    room = rooms.rooms.get(room_id.upper())
    token = websocket.query_params.get("token", "")
    is_spectator = room is not None and player_id in room.spectator_tokens
    stored_token = (
        room.spectator_tokens.get(player_id)
        if is_spectator and room is not None
        else room.player_tokens.get(player_id)
        if room is not None
        else None
    )
    if (
        room is None
        or stored_token is None
        or not secrets.compare_digest(stored_token, token)
    ):
        await websocket.close(code=1008, reason="Invalid room or player token.")
        return

    await websocket.accept()
    room.connections[player_id] = websocket
    # The newcomer always gets the current state; others only if it changed.
    await websocket.send_text(_state_message(room))
    await _broadcast(room)
    _ensure_room_loop(room)
    try:
        while True:
            message = await websocket.receive_text()
            try:
                _apply_action(room, player_id, is_spectator, message)
            except (ValueError, GameError) as error:
                await websocket.send_json({"type": "error", "message": str(error)})
    except WebSocketDisconnect:
        pass
    finally:
        if room.connections.get(player_id) is websocket:
            del room.connections[player_id]
        if not room.connections:
            await _stop_room_loop(room)


def _apply_action(
    room: Room, player_id: str, is_spectator: bool, message: str
) -> None:
    """Apply one client action. The room loop ticks and broadcasts the result."""
    action = GameAction.model_validate_json(message)
    if is_spectator:
        raise GameError("Spectators cannot control the match.")
    if not room.started:
        raise GameError("The room creator has not started the match.")
    now = time.monotonic()
    room.game.tick(now)
    if action.type == "move":
        if action.direction is None:
            raise GameError("A movement direction is required.")
        room.game.move(player_id, *DIRECTIONS[action.direction], now=now)
    elif action.type == "place_coil":
        room.game.place_coil(player_id, now)
    else:
        room.game.detonate_remote(player_id, now)


def _ensure_room_loop(room: Room) -> None:
    if room.loop_task is None or room.loop_task.done():
        room.loop_task = asyncio.create_task(_run_room_loop(room))


async def _stop_room_loop(room: Room) -> None:
    task = room.loop_task
    room.loop_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _run_room_loop(room: Room) -> None:
    """Advance one room's match at a fixed rate and broadcast state changes."""
    next_tick = time.monotonic()
    while room.connections:
        now = time.monotonic()
        if room.phase == "playing":
            for bot in room.bots.values():
                bot.act(room.game, now)
        room.game.tick(now)
        await _broadcast(room)
        next_tick += TICK_SECONDS
        delay = next_tick - time.monotonic()
        if delay < 0:
            # Fell behind (e.g. a slow send); resynchronize instead of bursting.
            next_tick = time.monotonic()
            delay = 0
        await asyncio.sleep(delay)


def _state_message(room: Room) -> str:
    state = room.game.snapshot()
    state.update(
        {
            "phase": room.phase,
            "creator_id": room.creator_id,
            "spectator_count": len(room.spectator_names),
            "spectators": list(room.spectator_names.values()),
        }
    )
    return json.dumps({"type": "state", "state": state})


async def _broadcast(room: Room) -> None:
    """Send the room state to every connection, but only if it has changed."""
    message = _state_message(room)
    if message == room.last_state_message:
        return
    room.last_state_message = message
    stale: list[str] = []
    for player_id, connection in list(room.connections.items()):
        try:
            await connection.send_text(message)
        except Exception:  # noqa: BLE001 - any send failure means a dead socket
            stale.append(player_id)
    for player_id in stale:
        room.connections.pop(player_id, None)


app.mount("/", StaticFiles(directory=WEB_DIRECTORY), name="web")
