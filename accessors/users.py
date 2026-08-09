from sqlalchemy import select

from database.session import async_session
from database.models import QueueEntry, UserModel


async def get_or_create_user(user_id: int, username: str | None = None) -> UserModel:
    async with async_session() as session:
        user = await session.get(UserModel, user_id)
        if not user:
            user = UserModel(id=user_id, username=username)
            session.add(user)
            await session.commit()
        return user

async def get_user_by_position(queue_id: int, position: int):
    async with async_session() as session:
        result = await session.execute(
            select(UserModel)
            .join(QueueEntry)
            .filter(QueueEntry.queue_id == queue_id, QueueEntry.position == position)
        )
        user = result.scalar_one_or_none()

        return user