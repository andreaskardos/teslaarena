from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from hmac import compare_digest

from starlette.websockets import WebSocket

from tesla_arena.game import (
    DEFAULT_ARENA_SIZE,
    DEFAULT_SOFT_BLOCK_PERCENTAGE,
    MAX_PLAYERS,
    GameError,
    GameState,
)


@dataclass
class Room:
    id: str
    private: bool
    invite_code: str | None
    game: GameState
    creator_id: str | None = None
    started: bool = False
    player_tokens: dict[str, str] = field(default_factory=dict)
    spectator_tokens: dict[str, str] = field(default_factory=dict)
    spectator_names: dict[str, str] = field(default_factory=dict)
    connections: dict[str, WebSocket] = field(default_factory=dict)
    _next_spectator_id: int = 1

    @property
    def phase(self) -> str:
        if not self.started:
            return "lobby"
        if sum(player.alive for player in self.game.players.values()) <= 1:
            return "finished"
        return "playing"

    def public_info(self) -> dict[str, object]:
        return {
            "id": self.id,
            "players": len(self.game.players),
            "max_players": self.game.player_limit,
            "spectators": len(self.spectator_names),
            "phase": self.phase,
            "width": self.game.width,
            "height": self.game.height,
            "soft_block_count": self.game.soft_block_count,
            "soft_block_percentage": self.game.soft_block_percentage,
        }


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create_room(
        self,
        private: bool,
        soft_block_count: int | None = None,
        *,
        arena_size: int = DEFAULT_ARENA_SIZE,
        player_limit: int = MAX_PLAYERS,
        soft_block_percentage: float = DEFAULT_SOFT_BLOCK_PERCENTAGE,
    ) -> tuple[Room, str | None]:
        room_id = secrets.token_hex(3).upper()
        while room_id in self.rooms:
            room_id = secrets.token_hex(3).upper()
        invite_code = secrets.token_urlsafe(6) if private else None
        room = Room(
            id=room_id,
            private=private,
            invite_code=invite_code,
            game=GameState.create(
                width=arena_size,
                height=arena_size,
                player_limit=player_limit,
                soft_block_count=soft_block_count,
                soft_block_percentage=(
                    None if soft_block_count is not None else soft_block_percentage
                ),
            ),
        )
        self.rooms[room.id] = room
        return room, invite_code

    def join_room(
        self, room_id: str, name: str, invite_code: str | None
    ) -> tuple[Room, str, str, str]:
        room = self.rooms.get(room_id.upper())
        if room is None:
            raise GameError("Room not found.")
        if room.private and (
            invite_code is None
            or room.invite_code is None
            or not compare_digest(invite_code, room.invite_code)
        ):
            raise PermissionError("A valid invitation code is required.")
        token = secrets.token_urlsafe(24)
        if room.started:
            clean_name = name.strip()
            if not clean_name or len(clean_name) > 20:
                raise GameError("Player name must be between 1 and 20 characters.")
            spectator_id = f"spectator-{room._next_spectator_id}"
            room._next_spectator_id += 1
            room.spectator_names[spectator_id] = clean_name
            room.spectator_tokens[spectator_id] = token
            return room, spectator_id, token, "spectator"

        player = room.game.join_player(name)
        room.player_tokens[player.id] = token
        role = "player"
        if room.creator_id is None:
            room.creator_id = player.id
            role = "creator"
        return room, player.id, token, role

    def start_room(self, room_id: str, player_id: str, token: str) -> Room:
        room = self.rooms.get(room_id.upper())
        if room is None:
            raise GameError("Room not found.")
        stored_token = room.player_tokens.get(player_id)
        if (
            stored_token is None
            or not compare_digest(stored_token, token)
            or player_id != room.creator_id
        ):
            raise PermissionError("Only the room creator can start the match.")
        if room.started:
            raise GameError("The match has already started.")
        if len(room.game.players) < 2:
            raise GameError("At least two players must join before starting.")
        room.started = True
        return room

    def public_rooms(self) -> list[dict[str, object]]:
        return [
            room.public_info()
            for room in self.rooms.values()
            if not room.private
        ]
