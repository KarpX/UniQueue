import secrets
import string

from database.session import async_session
from database.models import QueueEntry, QueueModel, RoomModel, RoomMember, UserModel, UserRole
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession


async def get_room_by_name_and_creator(room_name: str, user_id: int, session: AsyncSession | None = None) -> RoomModel | None:
    if session is not None:
        result = await session.execute(select(RoomModel).filter_by(name=room_name, creator_id=user_id))
        return result.scalar_one_or_none()
    async with async_session() as s:
        result = await s.execute(select(RoomModel).filter_by(name=room_name, creator_id=user_id))
        return result.scalar_one_or_none()


async def get_rooms_for_user(user_id: int, session: AsyncSession | None = None) -> list[RoomModel]:
    stmt = select(RoomModel).join(RoomModel.members).filter_by(user_id=user_id)
    if session is not None:
        result = await session.execute(stmt)
        return list(result.scalars().all())
    async with async_session() as s:
        result = await s.execute(stmt)
        return list(result.scalars().all())


async def get_room_by_id(room_id: int, session: AsyncSession | None = None) -> RoomModel | None:
    if session is not None:
        return await session.get(RoomModel, room_id)
    async with async_session() as s:
        return await s.get(RoomModel, room_id)


async def get_members_of_room(room_id: int, session: AsyncSession | None = None) -> list[UserModel]:
    stmt = select(UserModel).join(RoomMember).filter(RoomMember.room_id == room_id)
    if session is not None:
        result = await session.execute(stmt)
        return list(result.scalars().all())
    async with async_session() as s:
        result = await s.execute(stmt)
        return list(result.scalars().all())


async def get_room_member(room_id: int, except_user_id: int | None = None, session: AsyncSession | None = None):
    stmt = (select(RoomMember)
            .options(selectinload(RoomMember.user))
            .filter(RoomMember.room_id == room_id))

    if except_user_id:
        stmt = stmt.filter(RoomMember.user_id != except_user_id)

    if session is not None:
        result = await session.execute(stmt)
        return list(result.scalars().all())
    async with async_session() as s:
        result = await s.execute(stmt)
        return list(result.scalars().all())


async def get_room_member_by_user_id(room_id: int, user_id: int, session: AsyncSession | None = None) -> RoomMember | None:
    stmt = (
        select(RoomMember)
        .options(selectinload(RoomMember.user))
        .filter(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
    )
    if session is not None:
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    async with async_session() as s:
        result = await s.execute(stmt)
        return result.scalar_one_or_none()


async def create_room_with_unique_code(room_name: str, creator_id: int, session: AsyncSession | None = None) -> RoomModel:
    if session is not None:
        return await _create_room_impl(session, room_name, creator_id)
    async with async_session() as s:
        room = await _create_room_impl(s, room_name, creator_id)
        await s.commit()
        return room


async def _create_room_impl(session: AsyncSession, room_name: str, creator_id: int) -> RoomModel:
    for _ in range(50):
        invite_code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        existing_room = await session.execute(
            select(RoomModel).filter_by(invite_code=invite_code)
        )
        if not existing_room.scalar():
            break
    else:
        invite_code = secrets.token_hex(4).upper()

    from accessors.users import get_or_create_user
    await get_or_create_user(creator_id, session=session)

    room = RoomModel(name=room_name, invite_code=invite_code, creator_id=creator_id)
    room_member = RoomMember(user_id=creator_id, room=room, role=UserRole.ADMIN)
    session.add(room)
    session.add(room_member)
    await session.flush()
    return room


async def get_room_with_queue(queue_id: int, session: AsyncSession | None = None) -> RoomModel | None:
    async def _query(s: AsyncSession):
        result = await s.execute(select(QueueModel).filter(QueueModel.id == queue_id))
        queue = result.scalar_one_or_none()
        if not queue:
            return None
        room_stmt = (
            select(RoomModel)
            .options(selectinload(RoomModel.members).selectinload(RoomMember.user))
            .filter(RoomModel.id == queue.room_id)
        )
        room = await s.execute(room_stmt)
        return room.scalar_one_or_none()

    if session is not None:
        return await _query(session)
    async with async_session() as s:
        return await _query(s)


async def change_member_role(room_id: int, user_id: int, role: UserRole, session: AsyncSession | None = None) -> bool:
    async def _query(s: AsyncSession):
        result = await s.execute(
            select(RoomMember).filter(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
        )
        member = result.scalar_one_or_none()
        if not member or member.role == role:
            return False

        # Protect room creator from being demoted
        room = await s.get(RoomModel, room_id)
        if room and room.creator_id == user_id and role != UserRole.ADMIN:
            return False

        member.role = role
        return True

    if session is not None:
        success = await _query(session)
        if success:
            await session.flush()
        return success
    async with async_session() as s:
        success = await _query(s)
        if success:
            await s.commit()
        return success


async def delete_room_member(room_id: int, user_id: int, session: AsyncSession | None = None) -> bool | None:
    async def _query(s: AsyncSession):
        room = await s.get(RoomModel, room_id)
        if room and room.creator_id == user_id:
            # Cannot kick room creator
            return False

        result = await s.execute(
            select(RoomMember).filter(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
        )
        member = result.scalar_one_or_none()
        if member is None:
            return None

        # Clean up queue entries for this member across all queues in this room
        queues_res = await s.execute(select(QueueModel.id).filter_by(room_id=room_id))
        q_ids = queues_res.scalars().all()
        if q_ids:
            await s.execute(
                delete(QueueEntry).where(
                    QueueEntry.user_id == user_id,
                    QueueEntry.queue_id.in_(q_ids)
                )
            )

        await s.delete(member)
        return True

    if session is not None:
        res = await _query(session)
        if res:
            await session.flush()
        return res
    async with async_session() as s:
        res = await _query(s)
        if res:
            await s.commit()
        return res


async def delete_room(room_id: int, session: AsyncSession | None = None) -> bool:
    if session is not None:
        room = await session.get(RoomModel, room_id)
        if not room:
            return False
        await session.delete(room)
        await session.flush()
        return True

    async with async_session() as s:
        room = await s.get(RoomModel, room_id)
        if not room:
            return False
        await s.delete(room)
        await s.commit()
        return True


async def join_room_with_unicode(user_id: int, invite_code: str, session: AsyncSession | None = None):
    async def _query(s: AsyncSession):
        result = await s.execute(select(RoomModel).filter(RoomModel.invite_code == invite_code))
        room = result.scalar_one_or_none()
        if not room:
            return "not_found", None

        existing_res = await s.execute(
            select(RoomMember).filter(RoomMember.room_id == room.id, RoomMember.user_id == user_id)
        )
        if existing_res.scalar_one_or_none():
            return "already_in", room

        room_member = RoomMember(room_id=room.id, user_id=user_id, role=UserRole.MEMBER)
        s.add(room_member)
        return "success", room

    if session is not None:
        status, room = await _query(session)
        if status == "success":
            await session.flush()
        return status, room

    async with async_session() as s:
        status, room = await _query(s)
        if status == "success":
            await s.commit()
        return status, room


async def get_room_by_invite_code(invite_code: str, session: AsyncSession | None = None) -> RoomModel | None:
    stmt = select(RoomModel).filter(RoomModel.invite_code == invite_code)
    if session is not None:
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
    async with async_session() as s:
        res = await s.execute(stmt)
        return res.scalar_one_or_none()


async def bind_room_to_chat(room_id: int, chat_id: int, session: AsyncSession | None = None) -> RoomModel | None:
    if session is not None:
        room = await session.get(RoomModel, room_id)
        if room:
            room.telegram_chat_id = chat_id
            await session.flush()
        return room

    async with async_session() as s:
        room = await s.get(RoomModel, room_id)
        if room:
            room.telegram_chat_id = chat_id
            await s.commit()
        return room


async def get_room_by_chat_id(chat_id: int, session: AsyncSession | None = None) -> RoomModel | None:
    stmt = (
        select(RoomModel)
        .options(selectinload(RoomModel.queues), selectinload(RoomModel.members))
        .filter(RoomModel.telegram_chat_id == chat_id)
    )
    if session is not None:
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
    async with async_session() as s:
        res = await s.execute(stmt)
        return res.scalar_one_or_none()