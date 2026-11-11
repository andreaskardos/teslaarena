from collections import deque
from itertools import combinations

import pytest

from tesla_arena.game import DEFAULT_ARENA_SIZE, MAX_PLAYERS, GameError, GameState


def test_default_arena_has_twelve_safe_spawns_at_least_ten_tiles_apart() -> None:
    game = GameState.create()
    spawns = game.spawn_points()

    assert (game.width, game.height) == (DEFAULT_ARENA_SIZE, DEFAULT_ARENA_SIZE)
    assert len(spawns) == MAX_PLAYERS
    assert all(point not in game.hard_walls | game.soft_blocks for point in spawns)
    assert min(
        _shortest_path_length(game, left, right)
        for left, right in combinations(spawns, 2)
    ) >= 10


def test_game_accepts_twelve_players_and_rejects_thirteenth() -> None:
    game = GameState.create()
    for index in range(MAX_PLAYERS):
        game.join_player(f"Player {index}")

    with pytest.raises(GameError, match="full"):
        game.join_player("Too many")


def test_movement_cannot_leave_arena_or_cross_obstacles() -> None:
    game = GameState.create()
    player = game.join_player("Boundary")

    with pytest.raises(GameError, match="blocked"):
        game.move(player.id, -1, 0)
    with pytest.raises(GameError, match="one tile"):
        game.move(player.id, 2, 0)
    assert player.position == game.spawn_points()[0]


def test_player_can_place_four_coils_but_not_a_fifth() -> None:
    game = GameState.create()
    player = game.join_player("Coils")
    game.place_coil(player.id, 0)

    game.move(player.id, 1, 0)
    game.place_coil(player.id, 0)
    game.move(player.id, 1, 0)
    game.place_coil(player.id, 0)
    game.move(player.id, 1, 0)
    game.place_coil(player.id, 0)
    game.move(player.id, 1, 0)

    with pytest.raises(GameError, match="slots"):
        game.place_coil(player.id, 0)


def test_coil_explodes_in_cross_and_eliminates_hit_players() -> None:
    game = GameState.create()
    owner = game.join_player("Owner")
    target = game.join_player("Target")
    horizontal_target = game.join_player("Horizontal")
    owner.x, owner.y = 5, 5
    target.x, target.y = 5, 7
    horizontal_target.x, horizontal_target.y = 7, 5
    game.place_coil(owner.id, 10)
    for _ in range(3):
        game.move(owner.id, -1, 0)

    assert game.tick(12.49) == []
    assert game.tick(12.5) == [1]
    assert owner.lives == 1
    assert target.lives == 0
    assert horizontal_target.lives == 0
    assert game.snapshot()["winner_id"] == owner.id


def test_player_is_eliminated_by_their_own_coil_if_they_do_not_escape() -> None:
    game = GameState.create()
    player = game.join_player("Player")
    game.place_coil(player.id, 0)

    game.tick(2.5)

    assert player.lives == 0
    assert not player.alive


def test_hard_wall_stops_lightning_and_survives() -> None:
    game = GameState.create()
    player = game.join_player("Player")
    player.x, player.y = 5, 5
    game.hard_walls.add((6, 5))
    behind_wall = game.join_player("Safe")
    behind_wall.x, behind_wall.y = 7, 5
    game.place_coil(player.id, 0)

    game.tick(3)

    assert behind_wall.lives == 1
    assert (6, 5) in game.hard_walls


def test_soft_block_is_destroyed_and_stops_lightning() -> None:
    game = GameState.create()
    player = game.join_player("Player")
    player.x, player.y = 5, 5
    game.soft_blocks.add((6, 5))
    behind_block = game.join_player("Safe")
    behind_block.x, behind_block.y = 7, 5
    game.place_coil(player.id, 0)

    game.tick(3)

    assert (6, 5) not in game.soft_blocks
    assert behind_block.lives == 1


def test_invalid_arena_dimensions_and_player_limit_are_rejected() -> None:
    with pytest.raises(GameError, match="at least 7"):
        GameState.create(width=5)
    with pytest.raises(GameError, match="between 2 and 12"):
        GameState.create(player_limit=13)


def _shortest_path_length(
    game: GameState, start: tuple[int, int], end: tuple[int, int]
) -> int:
    queue = deque([(start, 0)])
    visited = {start}
    blocked = game.hard_walls | game.soft_blocks
    while queue:
        (x, y), distance = queue.popleft()
        if (x, y) == end:
            return distance
        for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbor not in blocked and neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
    raise AssertionError(f"No route between spawns {start} and {end}.")
