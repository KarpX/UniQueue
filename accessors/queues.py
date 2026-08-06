from database.models import QueueEntry, QueueModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload


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
