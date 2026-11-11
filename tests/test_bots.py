import random

import pytest

from tesla_arena.bots import BOT_SKILLS, Bot, danger_map
from tesla_arena.game import GameState
from tesla_arena.rooms import RoomManager


def _solo_room(player_limit: int):
    manager = RoomManager()
    room, _ = manager.create_room(private=False, player_limit=player_limit)
    _, host_id, token, _ = manager.join_room(room.id, "Solo", None)
    manager.start_room(room.id, host_id, token)
    return room


@pytest.mark.parametrize(
    ("player_limit", "skills"),
    [
        (12, ["easy", "medium", "hard"]),
        (3, ["easy", "hard"]),
        (2, ["medium"]),
    ],
)
def test_lone_creator_gets_bots_with_different_skills(player_limit, skills) -> None:
    room = _solo_room(player_limit)

    bots = [player for player in room.game.players.values() if player.bot_skill]
    assert room.started
    assert [bot.bot_skill for bot in bots] == skills
    assert set(room.bots) == {bot.id for bot in bots}
    assert len({bot.name for bot in bots}) == len(bots)
    assert not room.player_tokens.keys() & room.bots.keys()


def _arena_with_bot(skill: str) -> tuple[GameState, Bot]:
    game = GameState.create(width=11, height=11, player_limit=2, soft_block_count=0)
    player = game.join_player("Bot")
    game.join_player("Idle")
    return game, Bot(player.id, BOT_SKILLS[skill], random.Random(1))


def _simulate(game: GameState, bot: Bot, seconds: float, start: float = 0) -> None:
    now = start
    while now < start + seconds:
        bot.act(game, now)
        game.tick(now)
        now += 0.05


def test_hard_bot_escapes_its_own_coil() -> None:
    game, bot = _arena_with_bot("hard")
    game.place_coil(bot.player_id, now=0)

    _simulate(game, bot, seconds=3)

    assert not game.coils, "the coil should have exploded"
    assert game.players[bot.player_id].alive


def test_hard_bot_does_not_place_a_coil_without_an_escape() -> None:
    game, bot = _arena_with_bot("hard")
    player = game.players[bot.player_id]
    # Box the bot into its corner spawn with soft blocks it would like to hit.
    for dx, dy in ((1, 0), (0, 1)):
        game.soft_blocks.add((player.x + dx, player.y + dy))

    _simulate(game, bot, seconds=1)

    assert not game.coils
    assert player.alive


def test_only_hard_bots_foresee_chain_reactions() -> None:
    game, bot = _arena_with_bot("hard")
    owner = game.players[bot.player_id]
    owner.coil_capacity = 2
    owner.x, owner.y = (5, 5)
    game.place_coil(owner.id, now=0)
    owner.x, owner.y = (5, 7)
    game.place_coil(owner.id, now=0)
    game.coils[1].explodes_at = 0.5
    game.coils[2].explodes_at = 2.5
    owner.x, owner.y = (1, 1)
    beyond_second_coil = (5, 9)

    hard = danger_map(game, 0, "observer", chains=True)
    medium = danger_map(game, 0, "observer", chains=False)

    assert hard[beyond_second_coil] == pytest.approx(0.5)
    assert medium[beyond_second_coil] == pytest.approx(2.5)


def test_bots_only_match_makes_progress() -> None:
    game = GameState.create(
        width=25, height=25, player_limit=3, soft_block_percentage=30
    )
    bots = []
    for index, skill in enumerate(("easy", "medium", "hard")):
        player = game.join_player(f"Bot {index}")
        bots.append(Bot(player.id, BOT_SKILLS[skill], random.Random(index)))
    blocks_at_start = len(game.soft_blocks)

    now = 0.0
    while now < 60:
        for bot in bots:
            bot.act(game, now)
        game.tick(now)
        now += 0.05

    eliminated = sum(not player.alive for player in game.players.values())
    assert len(game.soft_blocks) < blocks_at_start - 10 or eliminated
