from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tesla_arena.game import DEFAULT_SOFT_BLOCK_COUNT, MAX_SOFT_BLOCK_COUNT, GameError
from tesla_arena.rooms import Room, RoomManager


class CreateRoomRequest(BaseModel):
    private: bool = False
    soft_block_count: int = Field(
        default=DEFAULT_SOFT_BLOCK_COUNT, ge=0, le=MAX_SOFT_BLOCK_COUNT
    )


class JoinRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    invite_code: str | None = None


class GameAction(BaseModel):
    type: Literal["move", "place_coil"]
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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "index.html")


@app.get("/api/rooms")
async def list_rooms() -> list[dict[str, object]]:
    return rooms.public_rooms()


@app.post("/api/rooms")
async def create_room(request: CreateRoomRequest) -> dict[str, object]:
    room, invite_code = rooms.create_room(
        request.private, request.soft_block_count
    )
    result: dict[str, object] = {
        "room_id": room.id,
        "max_players": room.game.player_limit,
        "soft_block_count": room.game.soft_block_count,
    }
    if invite_code is not None:
        result["invite_code"] = invite_code
    return result


@app.post("/api/rooms/{room_id}/join")
async def join_room(room_id: str, request: JoinRoomRequest) -> dict[str, str]:
    try:
        room, player_id, token = rooms.join_room(
            room_id, request.name, request.invite_code
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except GameError as error:
        status = 404 if "not found" in str(error).lower() else 409
        raise HTTPException(status_code=status, detail=str(error)) from error
    return {
        "room_id": room.id,
        "player_id": player_id,
        "token": token,
        "websocket_path": f"/ws/{room.id}/{player_id}",
    }


@app.websocket("/ws/{room_id}/{player_id}")
async def game_socket(websocket: WebSocket, room_id: str, player_id: str) -> None:
    room = rooms.rooms.get(room_id.upper())
    token = websocket.query_params.get("token", "")
    stored_token = room.player_tokens.get(player_id) if room is not None else None
    if (
        room is None
        or stored_token is None
        or not secrets.compare_digest(stored_token, token)
    ):
        await websocket.close(code=1008, reason="Invalid room or player token.")
        return

    await websocket.accept()
    room.connections[player_id] = websocket
    await _broadcast(room)
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
            except asyncio.TimeoutError:
                room.game.tick(time.monotonic())
                await _broadcast(room)
                continue

            try:
                action = GameAction.model_validate_json(message)
                if action.type == "move":
                    if action.direction is None:
                        raise GameError("A movement direction is required.")
                    room.game.move(
                        player_id, *DIRECTIONS[action.direction], now=time.monotonic()
                    )
                else:
                    room.game.place_coil(player_id, time.monotonic())
                room.game.tick(time.monotonic())
            except (ValueError, GameError) as error:
                await websocket.send_json({"type": "error", "message": str(error)})
            await _broadcast(room)
    except WebSocketDisconnect:
        room.connections.pop(player_id, None)


async def _broadcast(room: Room) -> None:
    message = {"type": "state", "state": room.game.snapshot()}
    stale: list[str] = []
    for player_id, connection in list(room.connections.items()):
        try:
            await connection.send_json(message)
        except WebSocketDisconnect:
            stale.append(player_id)
    for player_id in stale:
        room.connections.pop(player_id, None)
app.mount("/", StaticFiles(directory=WEB_DIRECTORY), name="web")
