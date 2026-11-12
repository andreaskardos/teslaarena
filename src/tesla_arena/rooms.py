from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Literal

from starlette.websockets import WebSocket

from tesla_arena.bots import BOT_LINEUP, BOT_SKILLS, Bot, bot_name, next_bot_name
from tesla_arena.game import (
    DEFAULT_ARENA_SIZE,
    DEFAULT_PLAYER_LIMIT,
    DEFAULT_SOFT_BLOCK_PERCENTAGE,
    GameError,
    GameState,
    Player,
)

# How long a member may stay disconnected before losing their place.
DISCONNECT_GRACE_SECONDS = 15.0
# How long a room without any members is kept before it is deleted.
EMPTY_ROOM_SECONDS = 30.0
# Caps that keep one server's memory use bounded.
MAX_ROOMS = 200
MAX_SPECTATORS_PER_ROOM = 50


class RoomLimitError(GameError):
    """The server cannot host more rooms right now."""


@dataclass
class MatchResult:
    winner: str | None
    """The winner's name, or None for a draw."""
    players: int
    duration_seconds: float
    ended_at: float
    """Wall-clock time (Unix seconds) when the match ended."""

    def as_dict(self) -> dict[str, object]:
        return {
            "winner": self.winner,
            "players": self.players,
            "duration_seconds": round(self.duration_seconds),
            "ended_at": self.ended_at,
        }


@dataclass
class Room:
    id: str
    game: GameState
    creator_id: str | None = None
    started: bool = False
    created_at: float = field(default_factory=time.time)
    """Wall-clock time (Unix seconds) when the room was created."""
    match_started_at: float | None = None
    """Monotonic time when the current match started."""
    last_result: MatchResult | None = None
    matches_played: int = 0
    player_tokens: dict[str, str] = field(default_factory=dict)
    spectator_tokens: dict[str, str] = field(default_factory=dict)
    spectator_names: dict[str, str] = field(default_factory=dict)
    connections: dict[str, WebSocket] = field(default_factory=dict)
    bots: dict[str, Bot] = field(default_factory=dict)
    disconnected_at: dict[str, float] = field(default_factory=dict)
    """Members (players or spectators) who may still reconnect, by drop time."""
    empty_since: float = field(default_factory=time.monotonic)
    loop_task: asyncio.Task[None] | None = None
    result_recorded: bool = False
    """Whether the current match's result is already in ``last_result``."""
    last_state_message: str | None = None
    _next_spectator_id: int = 1

    @property
    def phase(self) -> str:
        if not self.started:
            return "lobby"
        if sum(player.alive for player in self.game.players.values()) <= 1:
            return "finished"
        return "playing"

    @property
    def current_result(self) -> MatchResult | None:
        """The result of the current match once it has finished."""
        if self.phase != "finished" or not self.result_recorded:
            return None
        return self.last_result

    def record_result(self, now: float | None = None) -> None:
        """Store the outcome of the current match once, when it has finished."""
        if (
            self.phase != "finished"
            or self.match_started_at is None
            or self.result_recorded
        ):
            return
        now = time.monotonic() if now is None else now
        living = [player for player in self.game.players.values() if player.alive]
        winner = living[0].name if len(living) == 1 else None
        self.last_result = MatchResult(
            winner=winner,
            players=len(self.game.players),
            duration_seconds=max(0.0, now - self.match_started_at),
            ended_at=time.time(),
        )
        self.matches_played += 1
        self.result_recorded = True

    def start_match(self, now: float | None = None) -> None:
        self.started = True
        self.match_started_at = time.monotonic() if now is None else now
        self.result_recorded = False

    def match_seconds(self, now: float | None = None) -> int | None:
        """Seconds played in the current match, frozen once it has finished."""
        if self.match_started_at is None:
            return None
        result = self.current_result
        if result is not None:
            return round(result.duration_seconds)
        now = time.monotonic() if now is None else now
        return round(max(0.0, now - self.match_started_at))

    def stats(self) -> dict[str, object]:
        """Room history that does not change every second."""
        return {
            "created_at": self.created_at,
            "matches_played": self.matches_played,
            "last_result": self.last_result.as_dict() if self.last_result else None,
        }

    def public_info(self) -> dict[str, object]:
        self.record_result()
        return {
            "id": self.id,
            "players": len(self.game.players),
            "max_players": self.game.player_limit,
            "spectators": len(self.spectator_names),
            "phase": self.phase,
            "match_seconds": self.match_seconds() if self.phase == "playing" else None,
            **self.stats(),
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
        soft_block_count: int | None = None,
        *,
        arena_size: int = DEFAULT_ARENA_SIZE,
        player_limit: int = DEFAULT_PLAYER_LIMIT,
        soft_block_percentage: float = DEFAULT_SOFT_BLOCK_PERCENTAGE,
    ) -> Room:
        if len(self.rooms) >= MAX_ROOMS:
            raise RoomLimitError("The server is full. Try again later.")
        room_id = secrets.token_hex(3).upper()
        while room_id in self.rooms:
            room_id = secrets.token_hex(3).upper()
        room = Room(
            id=room_id,
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
        return room

    def join_room(
        self,
        room_id: str,
        name: str,
        now: float | None = None,
        *,
        as_spectator: bool = False,
    ) -> tuple[Room, str, str, Literal["creator", "player", "spectator"]]:
        """Join as a player, or as a spectator.

        Members join as spectators when they ask to, or once the match has
        started.

        The member must connect within the reconnect grace period or lose
        their place, so abandoned joins do not hold player slots.
        """
        now = time.monotonic() if now is None else now
        room = self.rooms.get(room_id.upper())
        if room is None:
            raise GameError("Room not found.")
        token = secrets.token_urlsafe(24)
        if as_spectator or room.started:
            clean_name = name.strip()
            if not clean_name or len(clean_name) > 20:
                raise GameError("Player name must be between 1 and 20 characters.")
            if len(room.spectator_tokens) >= MAX_SPECTATORS_PER_ROOM:
                raise GameError("This room has no more spectator places.")
            spectator_id = f"spectator-{room._next_spectator_id}"
            room._next_spectator_id += 1
            room.spectator_names[spectator_id] = clean_name
            room.spectator_tokens[spectator_id] = token
            room.disconnected_at[spectator_id] = now
            return room, spectator_id, token, "spectator"

        player = room.game.join_player(name)
        room.player_tokens[player.id] = token
        room.disconnected_at[player.id] = now
        role: Literal["creator", "player", "spectator"] = "player"
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
        room.start_match()
        return room

    def rematch(self, room_id: str, token: str) -> Room:
        """Return a finished room to its lobby with a fresh arena."""
        room = self._room_for_creator(room_id, token, "start a rematch")
        if room.phase != "finished":
            raise GameError("The match is not over yet.")
        room.record_result()
        room.game = room.game.rematch()
        room.started = False
        room.match_started_at = None
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
        return [room.public_info() for room in self.rooms.values()]
