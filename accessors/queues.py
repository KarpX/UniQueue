from database.models import QueueEntry, QueueModel, SwapRequest
from sqlalchemy import or_, select
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

async def swap_users_in_queue_by_pos(queue_id: int, pos_1: int, pos_2: int):
    async with async_session() as session:
        result = await session.execute(
            select(QueueEntry)
            .options(selectinload(QueueEntry.user), selectinload(QueueEntry.queue))
            .filter(
                QueueEntry.queue_id == queue_id,
                QueueEntry.position.in_([pos_1, pos_2])
            )
        )
        entries = result.scalars().all()

        if len(entries) != 2:
            return False, None, None

        entries[0].position, entries[1].position = entries[1].position, entries[0].position

        await reindex_queue(session, queue_id)

        await session.commit()

        return True, entries[0], entries[1]

async def get_entry_by_position(queue_id: int, position: int, user_id: int | None = None):
    async with async_session() as session:
        qs = select(QueueEntry).options(selectinload(QueueEntry.user),
            selectinload(QueueEntry.queue)).filter(QueueEntry.queue_id == queue_id, QueueEntry.position == position)

        if user_id:
            qs = qs.filter(QueueEntry.user_id != user_id)

        result = await session.execute(qs)

        entry = result.scalar_one_or_none()

        return entry

async def get_entry_by_user_id(queue_id: int, user_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(QueueEntry)
            .options(selectinload(QueueEntry.queue))
            .filter(QueueEntry.queue_id == queue_id, QueueEntry.user_id == user_id)
        )
        entry = result.scalar_one_or_none()
        return entry

async def get_entry_with_user_by_user_id(queue_id: int, user_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(QueueEntry)
            .options(selectinload(QueueEntry.user), selectinload(QueueEntry.queue))
            .filter(QueueEntry.queue_id == queue_id, QueueEntry.user_id == user_id)
        )
        entry = result.scalar_one_or_none()
        return entry

async def has_active_swap_request(queue_id: int, user_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(SwapRequest)
            .options(selectinload(SwapRequest.queue), selectinload(SwapRequest.target), selectinload(SwapRequest.sender))
            .filter(SwapRequest.queue_id == queue_id, or_(SwapRequest.sender_id == user_id, SwapRequest.target_id == user_id))
        )
        swap_request = result.scalar_one_or_none()

        return swap_request

async def create_swap_request(queue_id: int, user_id: int, target_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(SwapRequest)
            .filter(SwapRequest.queue_id == queue_id, SwapRequest.sender_id == user_id)
        )
        swap_request = result.scalar_one_or_none()

        if swap_request is None:
            new_request = SwapRequest(queue_id = queue_id, sender_id = user_id, target_id = target_id)
            session.add(new_request)
            await session.commit()
            return new_request

        return None

async def get_swap_request_by_id(swap_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(SwapRequest)
            .filter(SwapRequest.id == swap_id)
        )
        swap_request = result.scalar_one_or_none()

        return swap_request

async def delete_swap_request(swap_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(SwapRequest)
            .filter(SwapRequest.id == swap_id)
        )
        swap_request = result.scalar_one_or_none()

        if swap_request:
            await session.delete(swap_request)
            await session.commit()
            return True

        return False

async def delete_swap_request_by_users(queue_id: int, user_id_from: int, user_id_to: int):
    async with async_session() as session:
        result = await session.execute(
            select(SwapRequest)
            .filter(SwapRequest.queue_id == queue_id,
                SwapRequest.sender_id == user_id_from,
                SwapRequest.target_id == user_id_to)
        )
        swap_request = result.scalar_one_or_none()

        if swap_request:
            await session.delete(swap_request)
            await session.commit()
            return True

        return False

async def get_queue_by_id(queue_id: int):
    async with async_session() as session:
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

async def update_msg_and_chat_ids(queue_id: int, message_id: int, chat_id: int):
    async with async_session() as session:
        queue = await session.get(QueueModel, queue_id)
        if queue:
            queue.last_msg_id = message_id
            queue.last_chat_id = chat_id
            await session.commit()

async def clear_msg_and_chat_ids(queue_id: int):
    async with async_session() as session:
        queue = await session.get(QueueModel, queue_id)
        if queue:
            queue.last_msg_id = None
            queue.last_chat_id = None
            await session.commit()