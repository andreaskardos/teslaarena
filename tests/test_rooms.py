import pytest

from tesla_arena import rooms
from tesla_arena.game import GameError
from tesla_arena.rooms import RoomManager


def test_joining_a_lobby_takes_a_player_slot_unless_spectating() -> None:
    manager = RoomManager()
    room = manager.create_room()

    joined_room, player_id, token, role = manager.join_room(room.id, "Host")
    _, watcher_id, _, watcher_role = manager.join_room(
        room.id, "Watcher", as_spectator=True
    )

    assert joined_room is room
    assert player_id in room.game.players
    assert token
    assert role == "creator"
    assert watcher_role == "spectator"
    assert watcher_id not in room.game.players
    assert room.spectator_names[watcher_id] == "Watcher"


def test_creator_starts_match_and_late_joiners_are_spectators() -> None:
    manager = RoomManager()
    room = manager.create_room()
    host_room, host_id, host_token, host_role = manager.join_room(
        room.id, "Host"
    )
    _, guest_id, _, guest_role = manager.join_room(room.id, "Guest")

    assert host_room is room
    assert host_role == "creator"
    assert guest_role == "player"
    with pytest.raises(PermissionError, match="room creator"):
        manager.start_room(room.id, host_id, host_token + "wrong")

    manager.start_room(room.id, host_id, host_token)
    joined_room, spectator_id, _, spectator_role = manager.join_room(
        room.id, "Viewer"
    )

    assert joined_room is room
    assert spectator_role == "spectator"
    assert spectator_id not in room.game.players
    assert room.spectator_names[spectator_id] == "Viewer"
    assert room.phase == "playing"
    assert len(room.game.players) == 2
    for player in room.game.players.values():
        player.lives = 0
    assert room.phase == "finished"


def test_every_room_is_listed_with_its_stats() -> None:
    manager = RoomManager()
    room = manager.create_room()

    [info] = manager.public_rooms()

    assert info["id"] == room.id
    assert info["max_players"] == 4
    assert info["created_at"] == room.created_at
    assert info["matches_played"] == 0
    assert info["last_result"] is None
    assert info["match_seconds"] is None


def test_finished_match_records_winner_and_duration() -> None:
    manager = RoomManager()
    room = manager.create_room()
    _, host_id, host_token, _ = manager.join_room(room.id, "Host")
    _, guest_id, _, _ = manager.join_room(room.id, "Guest")
    manager.start_room(room.id, host_id, host_token)
    room.match_started_at -= 90  # pretend the match has run for 90 seconds

    assert manager.public_rooms()[0]["match_seconds"] == 90
    room.game.players[guest_id].lives = 0
    [info] = manager.public_rooms()

    assert info["phase"] == "finished"
    assert info["matches_played"] == 1
    assert info["last_result"]["winner"] == "Host"
    assert info["last_result"]["players"] == 2
    assert info["last_result"]["duration_seconds"] == 90
    # Recording again does not count the same match twice.
    room.record_result()
    assert room.matches_played == 1

    manager.rematch(room.id, host_token)
    assert room.match_seconds() is None
    assert manager.public_rooms()[0]["last_result"]["winner"] == "Host"


def test_match_counts_down_before_it_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rooms, "COUNTDOWN_SECONDS", 3.0)
    room = RoomManager().create_room()
    room.game.join_player("Host")
    room.game.join_player("Guest")

    room.start_match(now=100.0)

    assert room.started
    assert room.countdown_seconds(now=100.0) == 3
    assert room.countdown_seconds(now=101.5) == 2
    assert room.countdown_seconds(now=102.9) == 1
    assert room.countdown_seconds(now=103.0) is None
    assert room.match_seconds(now=101.0) == 0
    assert room.match_seconds(now=105.0) == 2


def test_timer_counts_down_the_match_then_the_closing_arena() -> None:
    manager = RoomManager()
    room = manager.create_room(time_limit_seconds=90)
    _, host_id, token, _ = manager.join_room(room.id, "Host")
    manager.join_room(room.id, "Guest")
    assert room.timer() is None  # lobby

    manager.start_room(room.id, host_id, token)
    assert room.game.closing_starts_at == room.match_started_at + 90
    # Restart from a whole-number clock so the expected seconds are exact; the
    # real monotonic clock can make (start + 150) - (start + 90) round to 60.0000001.
    start = 1000.0
    room.start_match(now=start)
    assert room.game.closing_starts_at == start + 90

    assert room.timer(now=start) == {"stage": "match", "seconds": 90}
    assert room.timer(now=start + 30.2) == {"stage": "match", "seconds": 60}
    assert room.timer(now=start + 90) == {"stage": "closing", "seconds": 60}
    assert room.timer(now=start + 145.5) == {"stage": "closing", "seconds": 5}
    assert room.public_info()["time_limit_seconds"] == 90


def test_rooms_without_a_timer_never_close() -> None:
    manager = RoomManager()
    room = manager.create_room()
    _, host_id, token, _ = manager.join_room(room.id, "Host")
    manager.join_room(room.id, "Guest")
    manager.start_room(room.id, host_id, token)

    assert room.timer() is None
    assert room.game.closing_starts_at is None


@pytest.mark.parametrize("seconds", [0, 9, 100 * 60])
def test_match_timer_must_be_in_range(seconds: int) -> None:
    with pytest.raises(GameError, match="match timer"):
        RoomManager().create_room(time_limit_seconds=seconds)


def test_rematch_restarts_the_match_timer() -> None:
    manager = RoomManager()
    room = manager.create_room(time_limit_seconds=60)
    _, host_id, token, _ = manager.join_room(room.id, "Host")
    _, guest_id, _, _ = manager.join_room(room.id, "Guest")
    manager.start_room(room.id, host_id, token)
    room.game.players[guest_id].lives = 0
    manager.rematch(room.id, token)
    assert room.game.closing_starts_at is None

    manager.start_room(room.id, host_id, token)
    assert room.game.closing_starts_at == room.match_started_at + 60
