import pytest

from tesla_arena.rooms import RoomManager


def test_private_room_requires_its_invitation_code_and_is_not_listed() -> None:
    manager = RoomManager()
    room, invite_code = manager.create_room(private=True)
    assert invite_code

    with pytest.raises(PermissionError, match="invitation"):
        manager.join_room(room.id, "Guest", "wrong")
    assert manager.public_rooms() == []

    joined_room, player_id, token = manager.join_room(room.id, "Guest", invite_code)
    assert joined_room is room
    assert player_id in room.game.players
    assert token


def test_public_rooms_are_discoverable_and_private_rooms_are_not() -> None:
    manager = RoomManager()
    public_room, _ = manager.create_room(private=False)
    manager.create_room(private=True)

    assert [item["id"] for item in manager.public_rooms()] == [public_room.id]
