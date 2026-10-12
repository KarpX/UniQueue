from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.session import async_session
from database.models import QueueEntry, UserModel


async def get_or_create_user(
    user_id: int, 
    username: str | None = None, 
    first_name: str | None = None, 
    last_name: str | None = None,
    session: AsyncSession | None = None
) -> UserModel:
    if session is not None:
        return await _get_or_create_user_impl(session, user_id, username, first_name, last_name)

    async with async_session() as s:
        user = await _get_or_create_user_impl(s, user_id, username, first_name, last_name)
        await s.commit()
        return user


async def _get_or_create_user_impl(
    session: AsyncSession, 
    user_id: int, 
    username: str | None, 
    first_name: str | None, 
    last_name: str | None
) -> UserModel:
    user = await session.get(UserModel, user_id)
    if not user:
        user = UserModel(
            id=user_id, 
            username=username, 
            first_name=first_name, 
            last_name=last_name
        )
        session.add(user)
    else:
        # Update user profile information if changed in Telegram
        changed = False
        if username is not None and user.username != username:
            user.username = username
            changed = True
        if first_name is not None and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if last_name is not None and user.last_name != last_name:
            user.last_name = last_name
            changed = True
        if changed:
            session.add(user)

    return user


async def get_user_by_position(queue_id: int, position: int, session: AsyncSession | None = None) -> UserModel | None:
    if session is not None:
        return await _get_user_by_pos_impl(session, queue_id, position)

    async with async_session() as s:
        return await _get_user_by_pos_impl(s, queue_id, position)


async def _get_user_by_pos_impl(session: AsyncSession, queue_id: int, position: int) -> UserModel | None:
    result = await session.execute(
        select(UserModel)
        .join(QueueEntry)
        .filter(QueueEntry.queue_id == queue_id, QueueEntry.position == position)
    )
    return result.scalar_one_or_none()