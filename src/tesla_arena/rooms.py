from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from hmac import compare_digest

from starlette.websockets import WebSocket

from tesla_arena.bots import BOT_LINEUP, BOT_SKILLS, Bot, bot_name, next_bot_name
from tesla_arena.game import (
    DEFAULT_ARENA_SIZE,
    DEFAULT_SOFT_BLOCK_PERCENTAGE,
    MAX_PLAYERS,
    GameError,
    GameState,
    Player,
)

# How long a member may stay disconnected before losing their place.
DISCONNECT_GRACE_SECONDS = 15.0
# How long a room without any members is kept before it is deleted.
EMPTY_ROOM_SECONDS = 30.0


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
    bots: dict[str, Bot] = field(default_factory=dict)
    disconnected_at: dict[str, float] = field(default_factory=dict)
    """Members (players or spectators) who may still reconnect, by drop time."""
    empty_since: float = field(default_factory=time.monotonic)
    loop_task: asyncio.Task[None] | None = None
    last_state_message: str | None = None
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
        self,
        room_id: str,
        name: str,
        invite_code: str | None,
        now: float | None = None,
    ) -> tuple[Room, str, str, str]:
        """Join as a player, or as a spectator once the match has started.

        The member must connect within the reconnect grace period or lose
        their place, so abandoned joins do not hold player slots.
        """
        now = time.monotonic() if now is None else now
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
            room.disconnected_at[spectator_id] = now
            return room, spectator_id, token, "spectator"

        player = room.game.join_player(name)
        room.player_tokens[player.id] = token
        room.disconnected_at[player.id] = now
        role = "player"
        if room.creator_id is None:
            room.creator_id = player.id
            role = "creator"
        return room, player.id, token, role

    def start_room(self, room_id: str, player_id: str, token: str) -> Room:
        room = self._lobby_room_for_creator(room_id, token, "start the match")
        if player_id != room.creator_id:
            raise PermissionError("Only the room creator can start the match.")
        if len(room.game.players) == 1:
            self._add_lineup(room)
        room.started = True
        return room

    def rematch(self, room_id: str, token: str) -> Room:
        """Return a finished room to its lobby with a fresh arena."""
        room = self._room_for_creator(room_id, token, "start a rematch")
        if room.phase != "finished":
            raise GameError("The match is not over yet.")
        room.game = room.game.rematch()
        room.started = False
        for bot in room.bots.values():
            bot.next_think_at = 0
        return room

    def add_bot(self, room_id: str, token: str, skill_key: str) -> Player:
        """Add a bot of the given skill to a room that is still in its lobby."""
        room = self._lobby_room_for_creator(room_id, token, "add bots")
        if skill_key not in BOT_SKILLS:
            raise GameError(f"Unknown bot skill: {skill_key}.")
        names_in_use = {
            room.game.players[bot_id].name.split(" (")[0] for bot_id in room.bots
        }
        return self._create_bot(room, next_bot_name(names_in_use), skill_key)

    def remove_bot(self, room_id: str, token: str, bot_id: str) -> None:
        room = self._lobby_room_for_creator(room_id, token, "remove bots")
        if bot_id not in room.bots:
            raise GameError("Bot not found.")
        room.game.remove_player(bot_id)
        del room.bots[bot_id]

    # Connections ---------------------------------------------------------

    def member_connected(self, room: Room, member_id: str) -> None:
        room.disconnected_at.pop(member_id, None)

    def member_disconnected(self, room: Room, member_id: str, now: float) -> None:
        """Start the reconnect grace period for a member who dropped."""
        if member_id in room.player_tokens or member_id in room.spectator_tokens:
            room.disconnected_at[member_id] = now
        if not room.connections:
            room.empty_since = now

    def leave(self, room: Room, member_id: str) -> None:
        """Remove a member at once, without a reconnect grace period."""
        room.disconnected_at.pop(member_id, None)
        self._remove_member(room, member_id)

    def sweep(self, now: float) -> list[Room]:
        """Expire reconnect grace periods and delete abandoned rooms.

        Returns the remaining rooms whose state changed.
        """
        changed: list[Room] = []
        for room in list(self.rooms.values()):
            expired = [
                member_id
                for member_id, since in room.disconnected_at.items()
                if now - since >= DISCONNECT_GRACE_SECONDS
            ]
            for member_id in expired:
                self.leave(room, member_id)
            abandoned = (
                not room.connections
                and not room.disconnected_at
                and now - room.empty_since >= EMPTY_ROOM_SECONDS
            )
            if abandoned:
                del self.rooms[room.id]
            elif expired:
                changed.append(room)
        return changed

    def _remove_member(self, room: Room, member_id: str) -> None:
        if room.spectator_tokens.pop(member_id, None) is not None:
            room.spectator_names.pop(member_id, None)
            return
        if room.player_tokens.pop(member_id, None) is None:
            return
        if room.started:
            # Keep them on the scoreboard, but out of the match.
            player = room.game.players[member_id]
            player.lives = 0
            player.left = True
        else:
            room.game.remove_player(member_id)
        if room.creator_id == member_id:
            room.creator_id = next(iter(room.player_tokens), None)

    # Authorization -------------------------------------------------------

    def _room_for_creator(self, room_id: str, token: str, action: str) -> Room:
        """Return the room after checking ``token`` belongs to its creator."""
        room = self.rooms.get(room_id.upper())
        if room is None:
            raise GameError("Room not found.")
        stored_token = (
            room.player_tokens.get(room.creator_id) if room.creator_id else None
        )
        if stored_token is None or not compare_digest(stored_token, token):
            raise PermissionError(f"Only the room creator can {action}.")
        return room

    def _lobby_room_for_creator(self, room_id: str, token: str, action: str) -> Room:
        room = self._room_for_creator(room_id, token, action)
        if room.started:
            raise GameError("The match has already started.")
        return room

    def _add_lineup(self, room: Room) -> None:
        """Give a lone creator up to three opponents of increasing skill."""
        free_slots = room.game.player_limit - len(room.game.players)
        # Small rooms keep the skills spread out rather than all hard bots.
        picks = {1: (1,), 2: (0, 2)}.get(free_slots, range(len(BOT_LINEUP)))
        for name, skill_key in (BOT_LINEUP[index] for index in picks):
            self._create_bot(room, name, skill_key)

    def _create_bot(self, room: Room, name: str, skill_key: str) -> Player:
        skill = BOT_SKILLS[skill_key]
        player = room.game.join_player(bot_name(name, skill))
        player.bot_skill = skill.key
        room.bots[player.id] = Bot(player.id, skill)
        return player

    def public_rooms(self) -> list[dict[str, object]]:
        return [
            room.public_info()
            for room in self.rooms.values()
            if not room.private
        ]
