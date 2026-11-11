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


def test_small_arena_default_block_count_fits_available_space() -> None:
    game = GameState.create(width=7, height=7, player_limit=2)

    assert len(game.soft_blocks) == game.soft_block_count
    assert game.soft_block_count < 16


@pytest.mark.parametrize("block_count", [0, 1, 16, 75, 200])
def test_arena_has_exact_requested_number_of_safe_soft_blocks(block_count: int) -> None:
    game = GameState.create(soft_block_count=block_count)
    spawn_points = game.spawn_points()

    assert game.soft_block_count == block_count
    assert len(game.soft_blocks) == block_count
    assert not game.soft_blocks & game.hard_walls
    assert all(
        abs(x - spawn_x) + abs(y - spawn_y) > 2
        for x, y in game.soft_blocks
        for spawn_x, spawn_y in spawn_points
    )


@pytest.mark.parametrize(
    ("size", "player_limit", "percentage"),
    [(25, 2, 0), (25, 8, 10), (35, 12, 2.3), (45, 6, 25), (45, 12, 100)],
)
def test_arena_block_percentage_uses_available_tiles_after_spawn_safety(
    size: int, player_limit: int, percentage: float
) -> None:
    game = GameState.create(
        width=size,
        height=size,
        player_limit=player_limit,
        soft_block_percentage=percentage,
    )
    safe_positions = [
        (x, y)
        for y in range(2, size - 2)
        for x in range(2, size - 2)
        if (x, y) not in game.hard_walls
        and all(
            abs(x - spawn_x) + abs(y - spawn_y) > 2
            for spawn_x, spawn_y in game.spawn_points()
        )
    ]
    expected_count = int(len(safe_positions) * percentage / 100 + 0.5)

    assert (game.width, game.height) == (size, size)
    assert game.player_limit == player_limit
    assert game.soft_block_percentage == percentage
    assert len(game.soft_blocks) == expected_count


@pytest.mark.parametrize("percentage", [-0.1, 100.1])
def test_arena_rejects_block_percentages_outside_supported_range(
    percentage: float,
) -> None:
    with pytest.raises(GameError, match="percentage must be between 0 and 100"):
        GameState.create(soft_block_percentage=percentage)


@pytest.mark.parametrize("block_count", [-1, 201])
def test_arena_rejects_soft_block_counts_outside_supported_range(
    block_count: int,
) -> None:
    with pytest.raises(GameError, match="between 0 and 200"):
        GameState.create(soft_block_count=block_count)


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


def test_player_starts_with_one_coil_slot() -> None:
    game = GameState.create()
    player = game.join_player("Coils")
    assert player.coil_capacity == 1
    game.place_coil(player.id, 0)

    game.move(player.id, 1, 0)
    with pytest.raises(GameError, match="slots"):
        game.place_coil(player.id, 0)


def test_bomb_up_drop_increases_capacity_and_allows_a_second_coil() -> None:
    game = GameState.create()
    game._random = Random(79)
    owner = game.join_player("Owner")
    owner.x, owner.y = 5, 5
    game.soft_blocks.add((6, 5))
    game.place_coil(owner.id, 0)
    for _ in range(3):
        game.move(owner.id, -1, 0)

    game.tick(3)

    assert game.powerups[(6, 5)] == "bomb"
    assert game.snapshot()["powerups"] == [{"x": 6, "y": 5, "type": "bomb"}]
    for _ in range(4):
        game.move(owner.id, 1, 0)

    assert owner.coil_capacity == 2
    assert game.snapshot()["players"][0]["coil_capacity"] == 2
    game.place_coil(owner.id, 4)
    game.move(owner.id, 1, 0)
    game.place_coil(owner.id, 5)

    assert len(game.coils) == 2
    assert game.snapshot()["players"][0]["coil_slots"] == 0
    with pytest.raises(GameError, match="slots"):
        game.place_coil(owner.id, 10)


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
    game._random = Random(28)
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


def test_speed_up_drop_increases_server_validated_movement_speed() -> None:
    game = GameState.create()
    game._random = Random(8)
    player = game.join_player("Runner")
    player.x, player.y = 5, 5
    game.soft_blocks.add((6, 5))
    game.place_coil(player.id, 0)
    for _ in range(3):
        game.move(player.id, -1, 0)

    game.tick(3)

    assert game.powerups[(6, 5)] == "speed"
    for _ in range(4):
        game.move(player.id, 1, 0)

    assert player.has_speed_up
    assert player.move_interval == pytest.approx(0.12)
    assert game.snapshot()["players"][0]["has_speed_up"] is True
    assert game.snapshot()["players"][0]["move_interval"] == pytest.approx(0.12)

    player.x, player.y = 5, 5
    game.move(player.id, 1, 0, now=10)
    with pytest.raises(GameError, match="too quickly"):
        game.move(player.id, 1, 0, now=10.05)
    game.move(player.id, 1, 0, now=10.12)
    assert player.position == (7, 5)


def test_remote_control_coils_wait_for_manual_detonation_and_chain() -> None:
    game = GameState.create()
    game.soft_blocks.clear()
    owner = game.join_player("Owner")
    target = game.join_player("Target")
    owner.x, owner.y = 5, 5
    owner.coil_capacity = 2
    target.x, target.y = 9, 5
    game.powerups[(6, 5)] = "remote"
    game.move(owner.id, 1, 0)
    assert owner.has_remote
    owner.x, owner.y = 5, 5
    first = game.place_coil(owner.id, 0)
    owner.x, owner.y = 7, 5
    chained = game.place_coil(owner.id, 0.1)
    owner.x, owner.y = 6, 7

    assert first.explodes_at is None
    assert chained.explodes_at is None
    assert game.tick(10) == []
    assert len(game.coils) == 2
    assert game.detonate_remote(owner.id, 10) == [first.id, chained.id]

    assert not game.coils
    assert owner.alive
    assert target.lives == 0


def test_remote_control_rejects_unavailable_or_empty_detonation() -> None:
    game = GameState.create()
    player = game.join_player("Player")

    with pytest.raises(GameError, match="not available"):
        game.detonate_remote(player.id, 0)

    player.has_remote = True
    with pytest.raises(GameError, match="no remote-controlled coils"):
        game.detonate_remote(player.id, 0)


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


def test_players_get_unique_colors_that_are_reused_and_kept_on_rematch() -> None:
    game = GameState.create(player_limit=12)
    players = [game.join_player(f"P{index}") for index in range(12)]
    assert sorted(player.color for player in players) == list(range(12))

    game.remove_player(players[3].id)
    newcomer = game.join_player("New")
    assert newcomer.color == players[3].color, "the freed color is reused"

    fresh = game.rematch()
    assert {pid: p.color for pid, p in fresh.players.items()} == {
        pid: p.color for pid, p in game.players.items()
    }
    assert {p["id"]: p["color"] for p in fresh.snapshot()["players"]} == {
        pid: p.color for pid, p in fresh.players.items()
    }


def test_slightly_early_moves_are_accepted_without_raising_speed() -> None:
    game = GameState.create(width=11, height=11, player_limit=2, soft_block_count=0)
    player = game.join_player("Runner")
    player.x, player.y = (1, 1)

    game.move(player.id, 1, 0, now=10.0)
    # Arrives 30 ms early because of jitter: accepted, schedule unchanged.
    game.move(player.id, 1, 0, now=10.17)
    assert player.next_move_at == pytest.approx(10.4)
    with pytest.raises(GameError, match="too quickly"):
        game.move(player.id, 1, 0, now=10.3)  # 100 ms early is still refused
    game.move(player.id, 1, 0, now=10.36)
    assert player.position == (4, 1)
    assert player.next_move_at == pytest.approx(10.6)
