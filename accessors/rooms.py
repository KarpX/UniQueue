from database.session import async_session
from database.models import RoomModel, RoomMember, UserModel, UserRole
from sqlalchemy import select
from sqlalchemy.orm import selectinload


async def get_room_by_name_and_creator(room_name: str, user_id: int) -> RoomModel | None:
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).filter_by(name=room_name, creator_id=user_id)
        )
        return result.scalar_one_or_none()


async def get_rooms_for_user(user_id: int) -> list[RoomModel]:
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).join(RoomModel.members).filter_by(user_id=user_id)
        )
        return result.scalars().all()


async def get_room_by_id(room_id: int) -> RoomModel | None:
    async with async_session() as session:
        return await session.get(RoomModel, room_id)


async def get_members_of_room(room_id: int) -> list[UserModel]:
    async with async_session() as session:
        result = await session.execute(
            select(UserModel).join(RoomMember).filter(RoomMember.room_id == room_id)
        )
        return result.scalars().all()


async def create_room_with_unique_code(room_name: str, creator_id: int) -> RoomModel:
    import secrets
    import string
    async with async_session() as session:
        while True:
            invite_code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
            existing_room = await session.execute(
                select(RoomModel).filter_by(invite_code=invite_code)
            )
            if not existing_room.scalar():
                break

        room = RoomModel(name=room_name, invite_code=invite_code, creator_id=creator_id)
        room_member = RoomMember(user_id=creator_id, room=room, role=UserRole.ADMIN)
        session.add(room)
        session.add(room_member)
        await session.commit()
        return room
