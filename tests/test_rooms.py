import pytest

from tesla_arena.rooms import RoomManager


def test_private_room_requires_its_invitation_code_and_is_not_listed() -> None:
    manager = RoomManager()
    room, invite_code = manager.create_room(private=True)
    assert invite_code

    with pytest.raises(PermissionError, match="invitation"):
        manager.join_room(room.id, "Guest", "wrong")
    assert manager.public_rooms() == []

    joined_room, player_id, token, role = manager.join_room(
        room.id, "Guest", invite_code
    )
    assert joined_room is room
    assert player_id in room.game.players
    assert token
    assert role == "creator"


def test_creator_starts_match_and_late_joiners_are_spectators() -> None:
    manager = RoomManager()
    room, _ = manager.create_room(private=False)
    host_room, host_id, host_token, host_role = manager.join_room(
        room.id, "Host", None
    )
    _, guest_id, _, guest_role = manager.join_room(room.id, "Guest", None)

    assert host_room is room
    assert host_role == "creator"
    assert guest_role == "player"
    with pytest.raises(PermissionError, match="room creator"):
        manager.start_room(room.id, host_id, host_token + "wrong")

    manager.start_room(room.id, host_id, host_token)
    joined_room, spectator_id, _, spectator_role = manager.join_room(
        room.id, "Viewer", None
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


def test_public_rooms_are_discoverable_and_private_rooms_are_not() -> None:
    manager = RoomManager()
    public_room, _ = manager.create_room(private=False)
    manager.create_room(private=True)

    assert [item["id"] for item in manager.public_rooms()] == [public_room.id]
