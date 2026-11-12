"""Computer-controlled players for Tesla Arena.

Bots act through the same ``GameState`` methods as human players, so they obey
movement speed, coil capacity and every other rule. Each skill profile changes
how quickly a bot reacts, how much danger it can see and how aggressive it is.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from random import Random

from tesla_arena.game import (
    COIL_FUSE_SECONDS,
    GameError,
    GameState,
    Player,
    Position,
)

STEPS: tuple[Position, ...] = ((0, -1), (0, 1), (-1, 0), (1, 0))
# How far ahead bots look at cells the closing arena is about to fill.
CLOSING_DANGER_SECONDS = 3.0
NEVER = float("inf")
# Extra time a bot leaves itself before lightning arrives.
SAFETY_MARGIN_SECONDS = 0.3
# How soon another player's remote-controlled coil might be triggered.
REMOTE_COIL_THREAT_SECONDS = 0.5
# How long a bot assumes its own remote-controlled coil will wait.
OWN_REMOTE_COIL_SECONDS = 10.0


@dataclass(frozen=True)
class BotSkill:
    key: str
    label: str
    think_seconds: float
    """Pause between decisions; also caps how fast the bot can walk."""
    mistake_chance: float
    """Chance per decision to take a random step instead of thinking."""
    escape_check_chance: float
    """Chance to check for an escape route before placing a coil."""
    sees_chain_reactions: bool
    """Whether a coil hit by lightning is treated as exploding at once."""
    collects_powerups: bool
    wander_chance: float
    """Chance per decision to wander instead of pursuing a target."""
    attack_chance: float
    """Chance to place a coil when one would hit a target."""


BOT_SKILLS: dict[str, BotSkill] = {
    "easy": BotSkill(
        key="easy",
        label="easy",
        think_seconds=0.45,
        mistake_chance=0.12,
        escape_check_chance=0.75,
        sees_chain_reactions=False,
        collects_powerups=False,
        wander_chance=0.4,
        attack_chance=0.5,
    ),
    "medium": BotSkill(
        key="medium",
        label="medium",
        think_seconds=0.28,
        mistake_chance=0.03,
        escape_check_chance=1.0,
        sees_chain_reactions=False,
        collects_powerups=True,
        wander_chance=0.1,
        attack_chance=0.8,
    ),
    "hard": BotSkill(
        key="hard",
        label="hard",
        think_seconds=0.12,
        mistake_chance=0.0,
        escape_check_chance=1.0,
        sees_chain_reactions=True,
        collects_powerups=True,
        wander_chance=0.0,
        attack_chance=1.0,
    ),
}

# Opponents added when a room creator starts alone, weakest first.
BOT_LINEUP: tuple[tuple[str, str], ...] = (
    ("Volt", "easy"),
    ("Ampere", "medium"),
    ("Tesla", "hard"),
)


# Names for bots the room creator adds by hand, used in this order.
BOT_NAMES: tuple[str, ...] = (
    "Volt",
    "Ampere",
    "Tesla",
    "Ohm",
    "Watt",
    "Faraday",
    "Joule",
    "Gauss",
    "Kelvin",
    "Hertz",
    "Coulomb",
)


def bot_name(name: str, skill: BotSkill) -> str:
    return f"{name} ({skill.label})"


def next_bot_name(names_in_use: set[str]) -> str:
    """First name from ``BOT_NAMES`` that no bot in the room uses yet."""
    for name in BOT_NAMES:
        if name not in names_in_use:
            return name
    return f"Bot {len(names_in_use) + 1}"


@dataclass
class Bot:
    player_id: str
    skill: BotSkill
    random: Random = field(default_factory=Random)
    next_think_at: float = 0

    def act(self, game: GameState, now: float) -> None:
        """Take at most one action if this bot is due to think."""
        if now < self.next_think_at:
            return
        self.next_think_at = now + self.skill.think_seconds
        player = game.players.get(self.player_id)
        if player is None or not player.alive:
            return
        try:
            self._decide(game, player, now)
        except GameError:
            # Another player may have moved into the way; retry next decision.
            pass

    def _decide(self, game: GameState, player: Player, now: float) -> None:
        danger = danger_map(
            game, now, self.player_id, chains=self.skill.sees_chain_reactions
        )
        here = player.position

        if here in danger:
            path = self._find_path(game, player, danger, lambda cell: cell not in danger)
            if path:
                self._move(game, player, path[0], now)
            return

        if self.random.random() < self.skill.mistake_chance:
            self._random_step(game, player, now)
            return

        if player.has_remote and self._should_detonate(game, player):
            game.detonate_remote(self.player_id, now)
            return

        if self._wants_coil(game, player) and self._can_escape_after_placing(
            game, player, danger
        ):
            game.place_coil(self.player_id, now)
            return

        if self.random.random() < self.skill.wander_chance:
            self._random_step(game, player, now, avoid=danger)
            return

        path = self._find_path(game, player, danger, self._goal_test(game, player))
        if path:
            self._move(game, player, path[0], now)
        elif path is None:
            # Nothing reachable to aim for; keep moving so the bot is not idle.
            self._random_step(game, player, now, avoid=danger)

    # Decisions -----------------------------------------------------------

    def _wants_coil(self, game: GameState, player: Player) -> bool:
        owned = sum(coil.owner_id == player.id for coil in game.coils.values())
        if owned >= player.coil_capacity:
            return False
        if any(coil.position == player.position for coil in game.coils.values()):
            return False
        cells = game.blast_cells(player.position, player.coil_range)
        hits_block = any(cell in game.soft_blocks for cell in cells)
        enemies = {
            other.position
            for other in game.players.values()
            if other.alive and other.id != player.id
        }
        hits_enemy = any(cell in enemies for cell in cells)
        return (hits_block or hits_enemy) and (
            self.random.random() < self.skill.attack_chance
        )

    def _can_escape_after_placing(
        self, game: GameState, player: Player, danger: dict[Position, float]
    ) -> bool:
        if self.random.random() >= self.skill.escape_check_chance:
            return True
        fuse = OWN_REMOTE_COIL_SECONDS if player.has_remote else COIL_FUSE_SECONDS
        future = dict(danger)
        for cell in game.blast_cells(player.position, player.coil_range):
            future[cell] = min(future.get(cell, NEVER), fuse)
        return bool(
            self._find_path(game, player, future, lambda cell: cell not in future)
        )

    def _should_detonate(self, game: GameState, player: Player) -> bool:
        remote_coils = [
            coil
            for coil in game.coils.values()
            if coil.owner_id == player.id and coil.remote_controlled
        ]
        if not remote_coils:
            return False
        own_blast = {
            cell
            for coil in remote_coils
            for cell in game.blast_cells(coil.position, player.coil_range)
        }
        return player.position not in own_blast

    def _goal_test(
        self, game: GameState, player: Player
    ) -> Callable[[Position], bool]:
        powerups = set(game.powerups) if self.skill.collects_powerups else set()
        near_block = {
            (x + dx, y + dy) for x, y in game.soft_blocks for dx, dy in STEPS
        }
        attack_cells = {
            cell
            for other in game.players.values()
            if other.alive and other.id != player.id
            for cell in game.blast_cells(other.position, player.coil_range)
        }
        targets = powerups | near_block | attack_cells
        # Standing still on a target is fine; the bot waits for a free coil.
        return lambda cell: cell in targets

    # Movement ------------------------------------------------------------

    def _move(
        self, game: GameState, player: Player, destination: Position, now: float
    ) -> None:
        if now < player.next_move_at:
            return
        dx = destination[0] - player.x
        dy = destination[1] - player.y
        game.move(self.player_id, dx, dy, now=now)

    def _random_step(
        self,
        game: GameState,
        player: Player,
        now: float,
        avoid: dict[Position, float] | None = None,
    ) -> None:
        options = [
            cell
            for cell in _neighbours(player.position)
            if _walkable(game, cell, player.id) and (avoid is None or cell not in avoid)
        ]
        if options:
            self._move(game, player, self.random.choice(options), now)

    def _find_path(
        self,
        game: GameState,
        player: Player,
        danger: dict[Position, float],
        is_goal: Callable[[Position], bool],
    ) -> list[Position] | None:
        """Shortest safe path to a goal: [] if already there, None if none.

        A path may cross a dangerous cell only if the bot will have left it
        before the lightning arrives. Goals must be outside the danger map.
        """
        start = player.position
        if start not in danger and is_goal(start):
            return []
        step_seconds = max(player.move_interval, self.skill.think_seconds)
        previous: dict[Position, Position] = {start: start}
        queue: deque[tuple[Position, int]] = deque([(start, 0)])
        while queue:
            cell, steps = queue.popleft()
            for neighbour in _neighbours(cell):
                if neighbour in previous or not _walkable(game, neighbour, player.id):
                    continue
                leaves_at = (steps + 2) * step_seconds + SAFETY_MARGIN_SECONDS
                if danger.get(neighbour, NEVER) <= leaves_at:
                    continue
                previous[neighbour] = cell
                if neighbour not in danger and is_goal(neighbour):
                    path = [neighbour]
                    while previous[path[-1]] != start:
                        path.append(previous[path[-1]])
                    return path[::-1]
                queue.append((neighbour, steps + 1))
        return None


def danger_map(
    game: GameState, now: float, viewer_id: str, *, chains: bool
) -> dict[Position, float]:
    """Seconds until lightning reaches each threatened cell, as seen by a bot."""
    explode_in: dict[int, float] = {}
    for coil in game.coils.values():
        if coil.explodes_at is not None:
            explode_in[coil.id] = max(0.0, coil.explodes_at - now)
        elif coil.owner_id == viewer_id:
            explode_in[coil.id] = OWN_REMOTE_COIL_SECONDS
        else:
            explode_in[coil.id] = REMOTE_COIL_THREAT_SECONDS

    blasts = {
        coil.id: game.blast_cells(
            coil.position, game.players[coil.owner_id].coil_range
        )
        for coil in game.coils.values()
    }
    if chains:
        coil_at = {coil.position: coil.id for coil in game.coils.values()}
        changed = True
        while changed:
            changed = False
            for coil_id, cells in blasts.items():
                for cell in cells:
                    other_id = coil_at.get(cell)
                    if other_id is not None and explode_in[other_id] > explode_in[coil_id]:
                        explode_in[other_id] = explode_in[coil_id]
                        changed = True

    danger: dict[Position, float] = {}
    for coil_id, cells in blasts.items():
        for cell in cells:
            danger[cell] = min(danger.get(cell, NEVER), explode_in[coil_id])
    # Cells the closing arena is about to wall up are as deadly as lightning.
    for cell, closes_in in game.closing_soon(now, CLOSING_DANGER_SECONDS).items():
        danger[cell] = min(danger.get(cell, NEVER), closes_in)
    return danger


def _neighbours(cell: Position) -> list[Position]:
    return [(cell[0] + dx, cell[1] + dy) for dx, dy in STEPS]


def _walkable(game: GameState, cell: Position, player_id: str) -> bool:
    if cell in game.hard_walls or cell in game.soft_blocks:
        return False
    if any(coil.position == cell for coil in game.coils.values()):
        return False
    return not any(
        other.alive and other.id != player_id and other.position == cell
        for other in game.players.values()
    )
