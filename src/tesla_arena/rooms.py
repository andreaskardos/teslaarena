from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from hmac import compare_digest

from starlette.websockets import WebSocket

from tesla_arena.game import DEFAULT_ARENA_SIZE, MAX_PLAYERS, GameError, GameState


@dataclass
class Room:
    id: str
    private: bool
    invite_code: str | None
    game: GameState
    player_tokens: dict[str, str] = field(default_factory=dict)
    connections: dict[str, WebSocket] = field(default_factory=dict)

    def public_info(self) -> dict[str, object]:
        return {
            "id": self.id,
            "players": len(self.game.players),
            "max_players": self.game.player_limit,
        }


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create_room(self, private: bool) -> tuple[Room, str | None]:
        room_id = secrets.token_hex(3).upper()
        while room_id in self.rooms:
            room_id = secrets.token_hex(3).upper()
        invite_code = secrets.token_urlsafe(6) if private else None
        room = Room(
            id=room_id,
            private=private,
            invite_code=invite_code,
            game=GameState.create(
                width=DEFAULT_ARENA_SIZE,
                height=DEFAULT_ARENA_SIZE,
                player_limit=MAX_PLAYERS,
            ),
        )
        self.rooms[room.id] = room
        return room, invite_code

    def join_room(
        self, room_id: str, name: str, invite_code: str | None
    ) -> tuple[Room, str, str]:
        room = self.rooms.get(room_id.upper())
        if room is None:
            raise GameError("Room not found.")
        if room.private and (
            invite_code is None
            or room.invite_code is None
            or not compare_digest(invite_code, room.invite_code)
        ):
            raise PermissionError("A valid invitation code is required.")
        player = room.game.join_player(name)
        token = secrets.token_urlsafe(24)
        room.player_tokens[player.id] = token
        return room, player.id, token

    def public_rooms(self) -> list[dict[str, object]]:
        return [
            room.public_info()
            for room in self.rooms.values()
            if not room.private and len(room.game.players) < room.game.player_limit
        ]
