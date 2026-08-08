from database.models import QueueEntry, QueueModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database.session import async_session


async def get_queue_with_data(session, queue_id: int):
    """Fetch QueueModel with its entries and user for entries using provided AsyncSession"""
    stmt = (
        select(QueueModel)
        .options(
            selectinload(QueueModel.entries)
            .selectinload(QueueEntry.user)
        )
        .filter(QueueModel.id == queue_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def reindex_queue(session, queue_id: int):
    stmt = (
        select(QueueEntry)
        .filter_by(queue_id=queue_id)
        .order_by(QueueEntry.position)
    )
    result = await session.execute(stmt)
    entries = result.scalars().all()

    for index, entry in enumerate(entries, start=1):
        entry.position = index


async def get_queues_by_room(session, room_id: int):
    stmt = select(QueueModel).filter(QueueModel.room_id == room_id)
    result = await session.execute(stmt)
    return result.scalars().all()


async def delete_queue(queue_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(QueueModel).filter(QueueModel.id == queue_id)
        )
        queue = result.scalar_one_or_none()

        if not queue:
            return None

        await session.delete(queue)
        await session.commit()

        return queue.room_id

async def swap_users_in_queue(session, queue_id: int, user_id_1: int, user_id_2: int):
    stmt = select(QueueEntry).filter(
        QueueEntry.queue_id == queue_id,
        QueueEntry.user_id.in_([user_id_1, user_id_2])
    )
    result = await session.execute(stmt)
    entries = result.scalars().all()

    if len(entries) != 2:
        return False

    entries[0].position, entries[1].position = entries[1].position, entries[0].position
    
    return True