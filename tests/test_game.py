from collections import deque
from itertools import combinations
from random import Random

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
    assert len(game.snapshot()["lightning"]) == 1
    assert [5, 5] in game.snapshot()["lightning"][0]["cells"]
    assert game.tick(13.16) == []
    assert game.snapshot()["lightning"] == []


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
    assert [6, 5] not in game.snapshot()["lightning"][0]["cells"]


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


def test_destroyed_soft_block_can_drop_kick_powerup_and_player_collects_it() -> None:
    game = GameState.create()
    game._random = Random(4)
    owner = game.join_player("Owner")
    owner.x, owner.y = 5, 5
    game.soft_blocks.add((6, 5))
    game.place_coil(owner.id, 0)
    for _ in range(3):
        game.move(owner.id, -1, 0)

    game.tick(3)

    assert game.powerups[(6, 5)] == "kick"
    assert game.snapshot()["powerups"] == [{"x": 6, "y": 5, "type": "kick"}]
    assert not owner.can_kick
    for _ in range(3):
        game.move(owner.id, 1, 0)
    game.move(owner.id, 1, 0)
    assert owner.can_kick
    assert game.powerups == {}


def test_fire_powerup_increases_range_and_extends_lightning() -> None:
    game = GameState.create()
    game._random = Random(8)
    owner = game.join_player("Owner")
    target = game.join_player("Target")
    owner.x, owner.y = 5, 5
    game.soft_blocks.add((6, 5))
    game.place_coil(owner.id, 0)
    for _ in range(3):
        game.move(owner.id, -1, 0)

    game.tick(3)

    assert game.powerups[(6, 5)] == "fire"
    for _ in range(4):
        game.move(owner.id, 1, 0)

    assert owner.coil_range == 3
    assert game.snapshot()["players"][0]["coil_range"] == 3
    owner.x, owner.y = 5, 5
    target.x, target.y = 8, 5
    game.place_coil(owner.id, 4)
    game.tick(6.5)

    assert target.lives == 0
    assert [8, 5] in game.snapshot()["lightning"][0]["cells"]


def test_kick_powerup_slides_coil_until_obstacle_and_player_takes_its_tile() -> None:
    game = GameState.create()
    player = game.join_player("Kicker")
    player.x, player.y = 5, 5
    player.can_kick = True
    coil = game.place_coil(player.id, 20)
    game.move(player.id, -1, 0)
    game.hard_walls.add((9, 5))

    game.move(player.id, 1, 0, now=0)

    assert player.position == (5, 5)
    assert coil.position == (6, 5)
    game.tick(0.15)
    assert coil.position == (7, 5)
    game.tick(0.30)
    assert coil.position == (8, 5)
    game.tick(0.46)
    assert coil.position == (8, 5)


def test_kicked_coil_explodes_at_its_current_position_while_moving() -> None:
    game = GameState.create()
    kicker = game.join_player("Kicker")
    target = game.join_player("Target")
    kicker.x, kicker.y = 5, 5
    kicker.can_kick = True
    coil = game.place_coil(kicker.id, 0)
    game.move(kicker.id, -1, 0)
    target.x, target.y = 7, 7

    game.move(kicker.id, 1, 0, now=2.2)
    assert coil.position == (6, 5)
    assert game.tick(2.49) == []
    assert coil.position == (7, 5)
    assert game.tick(2.5) == [coil.id]

    lightning = game.snapshot()["lightning"][0]
    assert lightning["center"] == [7, 5]
    assert target.lives == 0


def test_player_without_kick_cannot_move_through_a_coil() -> None:
    game = GameState.create()
    player = game.join_player("Player")
    player.x, player.y = 5, 5
    coil = game.place_coil(player.id, 20)
    game.move(player.id, -1, 0)

    with pytest.raises(GameError, match="blocked"):
        game.move(player.id, 1, 0)

    assert player.position == (4, 5)
    assert coil.position == (5, 5)


def test_kick_cannot_push_coil_into_a_wall() -> None:
    game = GameState.create()
    player = game.join_player("Kicker")
    player.x, player.y = 5, 5
    player.can_kick = True
    coil = game.place_coil(player.id, 20)
    game.move(player.id, -1, 0)
    game.hard_walls.add((6, 5))

    with pytest.raises(GameError, match="cannot be kicked"):
        game.move(player.id, 1, 0, now=0)

    assert player.position == (4, 5)
    assert coil.position == (5, 5)


def test_kick_stops_coil_before_another_player() -> None:
    game = GameState.create()
    kicker = game.join_player("Kicker")
    target = game.join_player("Target")
    kicker.x, kicker.y = 5, 5
    kicker.can_kick = True
    coil = game.place_coil(kicker.id, 20)
    game.move(kicker.id, -1, 0)
    target.x, target.y = 8, 5

    game.move(kicker.id, 1, 0, now=0)

    assert kicker.position == (5, 5)
    assert coil.position == (6, 5)
    game.tick(0.15)
    assert coil.position == (7, 5)
    game.tick(0.30)
    assert coil.position == (7, 5)
    assert target.position == (8, 5)


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
