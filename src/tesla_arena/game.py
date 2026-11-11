from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import floor
from typing import TypeAlias

Position: TypeAlias = tuple[int, int]

MAX_PLAYERS = 12
DEFAULT_ARENA_SIZE = 35
COIL_FUSE_SECONDS = 2.5
COIL_RANGE = 2
COIL_CAPACITY = 4
LIGHTNING_VISUAL_SECONDS = 0.65


class GameError(ValueError):
    """An invalid game action or configuration."""


@dataclass
class Player:
    id: str
    name: str
    x: int
    y: int
    lives: int = 1
    coil_capacity: int = COIL_CAPACITY
    coil_range: int = COIL_RANGE

    @property
    def alive(self) -> bool:
        return self.lives > 0

    @property
    def position(self) -> Position:
        return self.x, self.y


@dataclass
class TeslaCoil:
    id: int
    owner_id: str
    position: Position
    explodes_at: float


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
    hard_walls: set[Position] = field(default_factory=set)
    soft_blocks: set[Position] = field(default_factory=set)
    players: dict[str, Player] = field(default_factory=dict)
    coils: dict[int, TeslaCoil] = field(default_factory=dict)
    lightning_effects: list[LightningEffect] = field(default_factory=list)
    _next_player_id: int = 1
    _next_coil_id: int = 1

    @classmethod
    def create(
        cls,
        width: int = DEFAULT_ARENA_SIZE,
        height: int = DEFAULT_ARENA_SIZE,
        player_limit: int = MAX_PLAYERS,
    ) -> GameState:
        if width < 7 or height < 7:
            raise GameError("Arena dimensions must be at least 7 by 7.")
        if not 2 <= player_limit <= MAX_PLAYERS:
            raise GameError(f"Player limit must be between 2 and {MAX_PLAYERS}.")

        arena = cls(width, height, player_limit)
        spawn_points = arena.spawn_points()
        for y in range(height):
            for x in range(width):
                position = (x, y)
                if x in (0, width - 1) or y in (0, height - 1):
                    arena.hard_walls.add(position)
                elif x % 2 == 0 and y % 2 == 0:
                    arena.hard_walls.add(position)
                elif (
                    1 < x < width - 2
                    and 1 < y < height - 2
                    and x % 2 == 1
                    and y % 2 == 1
                    and (x * 17 + y * 31) % 13 == 0
                    and all(
                        abs(x - spawn_x) + abs(y - spawn_y) > 2
                        for spawn_x, spawn_y in spawn_points
                    )
                ):
                    arena.soft_blocks.add(position)
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
        occupied = {player.position for player in self.players.values()}
        spawn = next(
            (point for point in self.spawn_points() if point not in occupied), None
        )
        if spawn is None:
            raise GameError("No free spawn point is available.")
        player = Player(
            id=f"player-{self._next_player_id}",
            name=clean_name,
            x=spawn[0],
            y=spawn[1],
        )
        self._next_player_id += 1
        self.players[player.id] = player
        return player

    def move(self, player_id: str, dx: int, dy: int) -> Position:
        player = self._get_living_player(player_id)
        if abs(dx) + abs(dy) != 1:
            raise GameError("Players can move one tile in one cardinal direction.")
        destination = (player.x + dx, player.y + dy)
        occupied_by_player = any(
            other.alive and other.position == destination
            for other in self.players.values()
            if other.id != player.id
        )
        occupied_by_coil = any(
            coil.position == destination for coil in self.coils.values()
        )
        if (
            destination in self.hard_walls
            or destination in self.soft_blocks
            or occupied_by_player
            or occupied_by_coil
        ):
            raise GameError("That tile is blocked.")
        player.x, player.y = destination
        return destination

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
            explodes_at=now + COIL_FUSE_SECONDS,
        )
        self._next_coil_id += 1
        self.coils[coil.id] = coil
        return coil

    def tick(self, now: float) -> list[int]:
        self.lightning_effects = [
            effect
            for effect in self.lightning_effects
            if effect.expires_at > now
        ]
        pending = deque(
            coil.id for coil in self.coils.values() if coil.explodes_at <= now
        )
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
                LightningEffect(coil.position, cells, now + LIGHTNING_VISUAL_SECONDS)
            )
            for chained in list(self.coils.values()):
                if chained.id != coil.id and chained.position in cells:
                    pending.append(chained.id)
            del self.coils[coil.id]
            exploded.append(coil.id)
        return exploded

    def _lightning_cells(self, coil: TeslaCoil) -> set[Position]:
        cells = {coil.position}
        x, y = coil.position
        directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
        for dx, dy in directions:
            for distance in range(1, self.players[coil.owner_id].coil_range + 1):
                position = (x + dx * distance, y + dy * distance)
                if position in self.hard_walls:
                    break
                cells.add(position)
                if position in self.soft_blocks:
                    self.soft_blocks.remove(position)
                    break
        return cells

    def snapshot(self) -> dict[str, object]:
        living = [player for player in self.players.values() if player.alive]
        winner = living[0] if len(self.players) >= 2 and len(living) == 1 else None
        return {
            "width": self.width,
            "height": self.height,
            "player_limit": self.player_limit,
            "hard_walls": [list(position) for position in sorted(self.hard_walls)],
            "soft_blocks": [list(position) for position in sorted(self.soft_blocks)],
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "x": player.x,
                    "y": player.y,
                    "lives": player.lives,
                    "alive": player.alive,
                    "coil_slots": player.coil_capacity
                    - sum(
                        coil.owner_id == player.id for coil in self.coils.values()
                    ),
                }
                for player in self.players.values()
            ],
            "coils": [
                {"id": coil.id, "x": coil.position[0], "y": coil.position[1]}
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

    def _get_living_player(self, player_id: str) -> Player:
        player = self.players.get(player_id)
        if player is None or not player.alive:
            raise GameError("Player is missing or has been eliminated.")
        return player
