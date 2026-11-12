from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import floor
from random import Random
from typing import TypeAlias

Position: TypeAlias = tuple[int, int]

MAX_PLAYERS = 8
DEFAULT_PLAYER_LIMIT = 4
DEFAULT_ARENA_SIZE = 35
DEFAULT_SOFT_BLOCK_COUNT = 16
DEFAULT_SOFT_BLOCK_PERCENTAGE = 65.0
MAX_SOFT_BLOCK_COUNT = 200
COIL_FUSE_SECONDS = 2.5
COIL_RANGE = 2
COIL_CAPACITY = 1
MOVE_INTERVAL_SECONDS = 0.2
SPEED_UP_MOVE_INTERVAL_SECONDS = 0.12
KICK_COIL_STEP_SECONDS = 0.15
LIGHTNING_VISUAL_SECONDS = 0.65
# Network jitter can deliver a correctly paced move slightly early; accept it
# but keep the schedule, so the average speed never increases.
MOVE_EARLY_TOLERANCE_SECONDS = 0.05
POWERUP_DROP_CHANCE = 0.25
# How long the arena takes to close completely once the match timer runs out.
CLOSING_SECONDS = 60.0
# How far ahead the snapshot warns about cells that are about to close.
CLOSING_WARNING_SECONDS = 1.5
BLOCK_POWERUPS = ("kick", "fire", "bomb", "speed", "remote")


class GameError(ValueError):
    """An invalid game action or configuration."""


def spiral_cells(left: int, top: int, right: int, bottom: int) -> list[Position]:
    """Cells of a rectangle in a clockwise spiral from its top-left corner.

    Along the top row to the right, down the right column, back along the
    bottom row, up the left column, then the same again one ring further in.
    """
    cells: list[Position] = []
    while left <= right and top <= bottom:
        cells.extend((x, top) for x in range(left, right + 1))
        cells.extend((right, y) for y in range(top + 1, bottom + 1))
        if top < bottom:
            cells.extend((x, bottom) for x in range(right - 1, left - 1, -1))
        if left < right:
            cells.extend((left, y) for y in range(bottom - 1, top, -1))
        left, top, right, bottom = left + 1, top + 1, right - 1, bottom - 1
    return cells


@dataclass
class Player:
    id: str
    name: str
    x: int
    y: int
    lives: int = 1
    coil_capacity: int = COIL_CAPACITY
    coil_range: int = COIL_RANGE
    can_kick: bool = False
    has_speed_up: bool = False
    has_remote: bool = False
    next_move_at: float = 0
    bot_skill: str | None = None
    left: bool = False
    color: int = 0
    """Index into the client's player palette; unique within a match."""

    @property
    def alive(self) -> bool:
        return self.lives > 0

    @property
    def position(self) -> Position:
        return self.x, self.y

    @property
    def move_interval(self) -> float:
        return (
            SPEED_UP_MOVE_INTERVAL_SECONDS
            if self.has_speed_up
            else MOVE_INTERVAL_SECONDS
        )


@dataclass
class TeslaCoil:
    id: int
    owner_id: str
    position: Position
    explodes_at: float | None
    remote_controlled: bool = False
    kick_direction: Position | None = None
    next_kick_step_at: float | None = None


@dataclass
class LightningEffect:
    center: Position
    cells: set[Position]
    expires_at: float


@dataclass
class GameState:
    width: int
    height: int
    player_limit: int
    soft_block_count: int = DEFAULT_SOFT_BLOCK_COUNT
    soft_block_percentage: float = DEFAULT_SOFT_BLOCK_PERCENTAGE
    hard_walls: set[Position] = field(default_factory=set)
    soft_blocks: set[Position] = field(default_factory=set)
    powerups: dict[Position, str] = field(default_factory=dict)
    players: dict[str, Player] = field(default_factory=dict)
    coils: dict[int, TeslaCoil] = field(default_factory=dict)
    lightning_effects: list[LightningEffect] = field(default_factory=list)
    creation_options: dict[str, object] = field(default_factory=dict, repr=False)
    closing_starts_at: float | None = None
    """When the arena starts closing in, or None if the match has no timer."""
    closed_cells: set[Position] = field(default_factory=set)
    """Cells the closing arena has turned into walls (also in ``hard_walls``)."""
    _closing_order: list[Position] = field(default_factory=list, repr=False)
    _last_tick_at: float = field(default=0.0, repr=False)
    _random: Random = field(default_factory=Random, repr=False)
    _next_player_id: int = 1
    _next_coil_id: int = 1

    @classmethod
    def create(
        cls,
        width: int = DEFAULT_ARENA_SIZE,
        height: int = DEFAULT_ARENA_SIZE,
        player_limit: int = MAX_PLAYERS,
        soft_block_count: int | None = None,
        soft_block_percentage: float | None = None,
    ) -> GameState:
        if width < 7 or height < 7:
            raise GameError("Arena dimensions must be at least 7 by 7.")
        if not 2 <= player_limit <= MAX_PLAYERS:
            raise GameError(f"Player limit must be between 2 and {MAX_PLAYERS}.")
        if soft_block_count is not None and soft_block_percentage is not None:
            raise GameError(
                "Choose either a soft block count or a soft block percentage."
            )
        if (
            soft_block_count is not None
            and not 0 <= soft_block_count <= MAX_SOFT_BLOCK_COUNT
        ):
            raise GameError(
                f"Soft block count must be between 0 and {MAX_SOFT_BLOCK_COUNT}."
            )
        if soft_block_percentage is not None and not 0 <= soft_block_percentage <= 100:
            raise GameError("Soft block percentage must be between 0 and 100.")

        arena = cls(width, height, player_limit)
        arena.creation_options = {
            "width": width,
            "height": height,
            "player_limit": player_limit,
            "soft_block_count": soft_block_count,
            "soft_block_percentage": soft_block_percentage,
        }
        spawn_points = arena.spawn_points()
        for y in range(height):
            for x in range(width):
                position = (x, y)
                if x in (0, width - 1) or y in (0, height - 1):
                    arena.hard_walls.add(position)
                elif x % 2 == 0 and y % 2 == 0:
                    arena.hard_walls.add(position)
        safe_positions = [
            (x, y)
            for y in range(2, height - 2)
            for x in range(2, width - 2)
            if (x, y) not in arena.hard_walls
            and all(
                abs(x - spawn_x) + abs(y - spawn_y) > 2
                for spawn_x, spawn_y in spawn_points
            )
        ]
        original_layout = [
            (x, y)
            for x, y in safe_positions
            if x % 2 == 1 and y % 2 == 1 and (x * 17 + y * 31) % 13 == 0
        ]
        if soft_block_count is None:
            if soft_block_percentage is None:
                soft_block_count = min(DEFAULT_SOFT_BLOCK_COUNT, len(safe_positions))
            else:
                soft_block_count = min(
                    len(safe_positions),
                    int(len(safe_positions) * soft_block_percentage / 100 + 0.5),
                )
        arena.soft_block_count = soft_block_count
        arena.soft_block_percentage = (
            soft_block_percentage
            if soft_block_percentage is not None
            else soft_block_count / len(safe_positions) * 100
            if safe_positions
            else 0
        )
        if soft_block_percentage is not None:
            arena.soft_blocks = set(
                arena._random.sample(safe_positions, soft_block_count)
            )
        elif soft_block_count <= len(original_layout):
            arena.soft_blocks = set(
                arena._random.sample(original_layout, soft_block_count)
            )
        else:
            remaining_positions = [
                position
                for position in safe_positions
                if position not in original_layout
            ]
            additional_count = soft_block_count - len(original_layout)
            if additional_count > len(remaining_positions):
                raise GameError(
                    f"Soft block count exceeds this arena's capacity of "
                    f"{len(safe_positions)}."
                )
            arena.soft_blocks = set(original_layout) | set(
                arena._random.sample(remaining_positions, additional_count)
            )
        return arena

    def spawn_points(self) -> list[Position]:
        perimeter: list[Position] = []
        perimeter.extend((x, 1) for x in range(1, self.width - 1))
        perimeter.extend((self.width - 2, y) for y in range(2, self.height - 1))
        perimeter.extend((x, self.height - 2) for x in range(self.width - 3, 0, -1))
        perimeter.extend((1, y) for y in range(self.height - 3, 1, -1))
        if len(perimeter) < self.player_limit:
            raise GameError("Arena is too small for the selected number of players.")
        return [
            perimeter[floor(index * len(perimeter) / self.player_limit)]
            for index in range(self.player_limit)
        ]

    def join_player(self, name: str) -> Player:
        if len(self.players) >= self.player_limit:
            raise GameError("This room is full.")
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 20:
            raise GameError("Player name must be between 1 and 20 characters.")
        player = self._spawn_player(f"player-{self._next_player_id}", clean_name)
        self._next_player_id += 1
        return player

    def _spawn_player(
        self, player_id: str, name: str, color: int | None = None
    ) -> Player:
        occupied = {player.position for player in self.players.values()}
        spawn = next(
            (point for point in self.spawn_points() if point not in occupied), None
        )
        if spawn is None:
            raise GameError("No free spawn point is available.")
        if color is None:
            used = {player.color for player in self.players.values()}
            color = next(index for index in range(MAX_PLAYERS) if index not in used)
        player = Player(id=player_id, name=name, x=spawn[0], y=spawn[1], color=color)
        self.players[player.id] = player
        return player

    def rematch(self) -> GameState:
        """A fresh arena with the same settings and the players who stayed.

        Player ids are kept so existing tokens and connections stay valid.
        """
        fresh = GameState.create(**self.creation_options)  # type: ignore[arg-type]
        for player in self.players.values():
            if player.left:
                continue
            fresh._spawn_player(
                player.id, player.name, player.color
            ).bot_skill = player.bot_skill
        fresh._next_player_id = self._next_player_id
        return fresh

    def remove_player(self, player_id: str) -> None:
        """Remove a player and their coils, freeing their spawn point."""
        if self.players.pop(player_id, None) is None:
            raise GameError("Player not found.")
        self.coils = {
            coil_id: coil
            for coil_id, coil in self.coils.items()
            if coil.owner_id != player_id
        }

    def move(
        self, player_id: str, dx: int, dy: int, now: float | None = None
    ) -> Position:
        player = self._get_living_player(player_id)
        if abs(dx) + abs(dy) != 1:
            raise GameError("Players can move one tile in one cardinal direction.")
        if now is not None and now < player.next_move_at - MOVE_EARLY_TOLERANCE_SECONDS:
            raise GameError("Moving too quickly.")
        destination = (player.x + dx, player.y + dy)
        coil = next(
            (coil for coil in self.coils.values() if coil.position == destination),
            None,
        )
        if coil is not None:
            if not player.can_kick:
                raise GameError("That tile is blocked.")
            self._kick_coil(coil, dx, dy, now if now is not None else 0)
        occupied_by_player = any(
            other.alive and other.position == destination
            for other in self.players.values()
            if other.id != player.id
        )
        if (
            destination in self.hard_walls
            or destination in self.soft_blocks
            or occupied_by_player
        ):
            raise GameError("That tile is blocked.")
        player.x, player.y = destination
        powerup = self.powerups.pop(destination, None)
        if powerup == "kick":
            player.can_kick = True
        elif powerup == "fire":
            player.coil_range += 1
        elif powerup == "bomb":
            player.coil_capacity += 1
        elif powerup == "speed":
            player.has_speed_up = True
        elif powerup == "remote":
            player.has_remote = True
        if now is not None:
            player.next_move_at = max(now, player.next_move_at) + player.move_interval
        return destination

    def _kick_coil(self, coil: TeslaCoil, dx: int, dy: int, now: float) -> None:
        coil.kick_direction = (dx, dy)
        if not self._advance_kicked_coil(coil):
            coil.kick_direction = None
            raise GameError("The Tesla coil cannot be kicked in that direction.")
        coil.next_kick_step_at = now + KICK_COIL_STEP_SECONDS

    def _advance_kicked_coil(self, coil: TeslaCoil) -> bool:
        if coil.kick_direction is None:
            return False
        dx, dy = coil.kick_direction
        next_position = (coil.position[0] + dx, coil.position[1] + dy)
        if self._coil_path_is_blocked(next_position, coil.id):
            coil.kick_direction = None
            coil.next_kick_step_at = None
            return False
        coil.position = next_position
        return True

    def _coil_path_is_blocked(self, position: Position, coil_id: int) -> bool:
        return (
            position in self.hard_walls
            or position in self.soft_blocks
            or any(
                player.alive and player.position == position
                for player in self.players.values()
            )
            or any(
                other.id != coil_id and other.position == position
                for other in self.coils.values()
            )
        )

    def place_coil(self, player_id: str, now: float) -> TeslaCoil:
        player = self._get_living_player(player_id)
        owned_coils = sum(coil.owner_id == player_id for coil in self.coils.values())
        if owned_coils >= player.coil_capacity:
            raise GameError("No Tesla coil slots are available.")
        if any(coil.position == player.position for coil in self.coils.values()):
            raise GameError("A Tesla coil is already on this tile.")
        coil = TeslaCoil(
            id=self._next_coil_id,
            owner_id=player_id,
            position=player.position,
            explodes_at=None if player.has_remote else now + COIL_FUSE_SECONDS,
            remote_controlled=player.has_remote,
        )
        self._next_coil_id += 1
        self.coils[coil.id] = coil
        return coil

    def detonate_remote(self, player_id: str, now: float) -> list[int]:
        player = self._get_living_player(player_id)
        if not player.has_remote:
            raise GameError("Remote Control is not available.")
        coil_ids = [
            coil.id
            for coil in self.coils.values()
            if coil.owner_id == player_id and coil.remote_controlled
        ]
        if not coil_ids:
            raise GameError("There are no remote-controlled coils to detonate.")
        return self._explode_coils(coil_ids, now)

    # Closing arena ------------------------------------------------------

    def schedule_closing(self, starts_at: float) -> None:
        """Close the arena in a spiral over ``CLOSING_SECONDS`` from ``starts_at``."""
        self.closing_starts_at = starts_at
        self.closed_cells = set()
        # Only open cells, so the visible fill keeps a steady pace.
        self._closing_order = [
            cell
            for cell in spiral_cells(1, 1, self.width - 2, self.height - 2)
            if cell not in self.hard_walls
        ]

    @property
    def closing_ends_at(self) -> float | None:
        if self.closing_starts_at is None:
            return None
        return self.closing_starts_at + CLOSING_SECONDS

    def _closes_at(self, index: int) -> float:
        """When the cell at ``index`` in the closing order becomes a wall."""
        assert self.closing_starts_at is not None
        step = CLOSING_SECONDS / len(self._closing_order)
        return self.closing_starts_at + (index + 1) * step

    def closing_soon(self, now: float, horizon: float) -> dict[Position, float]:
        """Seconds until each cell closes, for cells closing within ``horizon``."""
        soon: dict[Position, float] = {}
        if self.closing_starts_at is None:
            return soon
        for index in range(len(self.closed_cells), len(self._closing_order)):
            closes_in = self._closes_at(index) - now
            if closes_in > horizon:
                break
            soon[self._closing_order[index]] = max(0.0, closes_in)
        return soon

    def _advance_closing(self, now: float) -> None:
        if self.closing_starts_at is None or now < self.closing_starts_at:
            return
        while len(self.closed_cells) < len(self._closing_order):
            index = len(self.closed_cells)
            if self._closes_at(index) > now:
                break
            self._close_cell(self._closing_order[index])

    def _close_cell(self, cell: Position) -> None:
        """Turn a cell into a wall, crushing whatever is on it."""
        self.closed_cells.add(cell)
        self.hard_walls.add(cell)
        self.soft_blocks.discard(cell)
        self.powerups.pop(cell, None)
        self.coils = {
            coil_id: coil
            for coil_id, coil in self.coils.items()
            if coil.position != cell
        }
        for player in self.players.values():
            if player.alive and player.position == cell:
                player.lives = 0

    def tick(self, now: float) -> list[int]:
        self._last_tick_at = now
        self._advance_closing(now)
        self.lightning_effects = [
            effect
            for effect in self.lightning_effects
            if effect.expires_at > now
        ]
        exploded: list[int] = []
        while True:
            due_events = []
            for coil in self.coils.values():
                if coil.explodes_at is not None and coil.explodes_at <= now:
                    due_events.append((coil.explodes_at, 0, coil.id, "explode"))
                if (
                    coil.next_kick_step_at is not None
                    and coil.next_kick_step_at <= now
                ):
                    due_events.append(
                        (coil.next_kick_step_at, 1, coil.id, "move")
                    )
            if not due_events:
                break

            event_at, _, coil_id, event_type = min(due_events)
            coil = self.coils.get(coil_id)
            if coil is None:
                continue
            if event_type == "move":
                if self._advance_kicked_coil(coil):
                    coil.next_kick_step_at += KICK_COIL_STEP_SECONDS
                continue

            exploded.extend(self._explode_coils([coil_id], event_at))
        return exploded

    def _explode_coils(self, coil_ids: list[int], now: float) -> list[int]:
        pending = deque(coil_ids)
        exploded: list[int] = []
        while pending:
            coil_id = pending.popleft()
            coil = self.coils.get(coil_id)
            if coil is None:
                continue
            cells = self._lightning_cells(coil)
            for player in self.players.values():
                if player.alive and player.position in cells:
                    player.lives -= 1
            self.lightning_effects.append(
                LightningEffect(
                    coil.position, cells, now + LIGHTNING_VISUAL_SECONDS
                )
            )
            for other in list(self.coils.values()):
                if other.id != coil.id and other.position in cells:
                    pending.append(other.id)
            del self.coils[coil.id]
            exploded.append(coil.id)
        return exploded

    def blast_cells(self, origin: Position, coil_range: int) -> list[Position]:
        """Cells a coil at ``origin`` would hit, without changing the arena."""
        cells = [origin]
        x, y = origin
        directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
        for dx, dy in directions:
            for distance in range(1, coil_range + 1):
                position = (x + dx * distance, y + dy * distance)
                if position in self.hard_walls:
                    break
                cells.append(position)
                if position in self.soft_blocks:
                    break
        return cells

    def _lightning_cells(self, coil: TeslaCoil) -> set[Position]:
        cells = self.blast_cells(
            coil.position, self.players[coil.owner_id].coil_range
        )
        for position in cells:
            if position in self.soft_blocks:
                self.soft_blocks.remove(position)
                if (
                    position not in self.powerups
                    and self._random.random() < POWERUP_DROP_CHANCE
                ):
                    self.powerups[position] = self._random.choice(BLOCK_POWERUPS)
        return set(cells)

    def snapshot(self) -> dict[str, object]:
        living = [player for player in self.players.values() if player.alive]
        winner = living[0] if len(self.players) >= 2 and len(living) == 1 else None
        return {
            "width": self.width,
            "height": self.height,
            "player_limit": self.player_limit,
            "soft_block_count": self.soft_block_count,
            "soft_block_percentage": self.soft_block_percentage,
            "soft_blocks_remaining": len(self.soft_blocks),
            "hard_walls": [
                list(position)
                for position in sorted(self.hard_walls - self.closed_cells)
            ],
            "closed_cells": [list(position) for position in sorted(self.closed_cells)],
            "closing_next": [
                list(position)
                for position in self._closing_soon_cells()
            ],
            "soft_blocks": [list(position) for position in sorted(self.soft_blocks)],
            "powerups": [
                {"x": position[0], "y": position[1], "type": powerup}
                for position, powerup in sorted(self.powerups.items())
            ],
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "x": player.x,
                    "y": player.y,
                    "lives": player.lives,
                    "alive": player.alive,
                    "can_kick": player.can_kick,
                    "coil_range": player.coil_range,
                    "coil_capacity": player.coil_capacity,
                    "move_interval": player.move_interval,
                    "has_speed_up": player.has_speed_up,
                    "has_remote": player.has_remote,
                    "bot_skill": player.bot_skill,
                    "left": player.left,
                    "color": player.color,
                    "coil_slots": player.coil_capacity
                    - sum(
                        coil.owner_id == player.id for coil in self.coils.values()
                    ),
                }
                for player in self.players.values()
            ],
            "coils": [
                {
                    "id": coil.id,
                    "x": coil.position[0],
                    "y": coil.position[1],
                    "remote_controlled": coil.remote_controlled,
                }
                for coil in self.coils.values()
            ],
            "lightning": [
                {
                    "center": list(effect.center),
                    "cells": [list(position) for position in sorted(effect.cells)],
                }
                for effect in self.lightning_effects
            ],
            "winner_id": winner.id if winner else None,
        }

    def _closing_soon_cells(self) -> list[Position]:
        """Cells that close within the warning window, as of the last tick."""
        return list(self.closing_soon(self._last_tick_at, CLOSING_WARNING_SECONDS))

    def _get_living_player(self, player_id: str) -> Player:
        player = self.players.get(player_id)
        if player is None or not player.alive:
            raise GameError("Player is missing or has been eliminated.")
        return player
