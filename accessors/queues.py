from datetime import datetime, timezone, timedelta
import logging
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from database.session import async_session
from database.models import QueueEntry, QueueModel, RoomMember, RoomModel, SwapRequest, UserModel, UserRole
from accessors.users import get_or_create_user

logger = logging.getLogger(__name__)


def get_utc_now():
    return datetime.now(timezone.utc)


async def get_queue_for_update(session: AsyncSession, queue_id: int) -> QueueModel | None:
    """Fetches QueueModel with entries and room, locking the row FOR UPDATE."""
    stmt = (
        select(QueueModel)
        .options(
            selectinload(QueueModel.entries).selectinload(QueueEntry.user),
            selectinload(QueueModel.room).selectinload(RoomModel.members)
        )
        .filter(QueueModel.id == queue_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_queue_with_data(session: AsyncSession, queue_id: int) -> QueueModel | None:
    stmt = (
        select(QueueModel)
        .options(
            selectinload(QueueModel.entries).selectinload(QueueEntry.user),
            selectinload(QueueModel.room).selectinload(RoomModel.members)
        )
        .filter(QueueModel.id == queue_id)
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_queue_by_id(queue_id: int, session: AsyncSession | None = None) -> QueueModel | None:
    if session is not None:
        return await get_queue_with_data(session, queue_id)
    async with async_session() as s:
        return await get_queue_with_data(s, queue_id)


async def reindex_queue(session: AsyncSession, queue_id: int):
    """Reindexes queue positions 1..N in sorted order."""
    stmt = (
        select(QueueEntry)
        .filter_by(queue_id=queue_id)
        .order_by(QueueEntry.position, QueueEntry.joined_at, QueueEntry.id)
    )
    result = await session.execute(stmt)
    entries = result.scalars().all()

    for index, entry in enumerate(entries, start=1):
        entry.position = index


async def ensure_user_in_room(
    session: AsyncSession, 
    user_id: int, 
    username: str | None, 
    first_name: str | None, 
    last_name: str | None, 
    room_id: int
) -> bool:
    """Ensures user exists and is added to the room."""
    await get_or_create_user(
        user_id=user_id, 
        username=username, 
        first_name=first_name, 
        last_name=last_name, 
        session=session
    )
    
    stmt = select(RoomMember).filter_by(user_id=user_id, room_id=room_id)
    result = await session.execute(stmt)
    membership = result.scalar_one_or_none()

    if not membership:
        new_member = RoomMember(
            user_id=user_id,
            room_id=room_id,
            role=UserRole.MEMBER
        )
        session.add(new_member)
        await session.flush()
        return False
    return True


async def atomic_join_queue(
    session: AsyncSession,
    queue_id: int,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None
) -> tuple[bool, str, QueueModel | None]:
    """Atomically joins a queue with FOR UPDATE row locking."""
    queue = await get_queue_for_update(session, queue_id)
    if not queue:
        return False, "Очередь не существует", None

    await ensure_user_in_room(session, user_id, username, first_name, last_name, queue.room_id)

    is_already_in = any(e.user_id == user_id for e in queue.entries)
    if is_already_in:
        return False, "Вы уже записаны в эту очередь!", queue

    next_position = max((e.position for e in queue.entries), default=0) + 1
    new_entry = QueueEntry(
        queue_id=queue_id,
        user_id=user_id,
        position=next_position,
        joined_at=get_utc_now()
    )
    session.add(new_entry)
    await session.flush()

    # Re-fetch locked queue to update entries list in session
    queue = await get_queue_for_update(session, queue_id)
    return True, "Вы успешно записались в очередь!", queue


async def atomic_exit_queue(
    session: AsyncSession,
    queue_id: int,
    user_id: int
) -> tuple[bool, str, QueueModel | None]:
    """Atomically exits a queue and cleans up swap requests."""
    queue = await get_queue_for_update(session, queue_id)
    if not queue:
        return False, "Очередь не существует", None

    entry = next((e for e in queue.entries if e.user_id == user_id), None)
    if not entry:
        return False, "Вас нет в этой очереди", queue

    await session.execute(
        delete(QueueEntry).filter_by(queue_id=queue_id, user_id=user_id)
    )

    # Cancel any active swap requests involving this user in this queue
    await session.execute(
        delete(SwapRequest).where(
            SwapRequest.queue_id == queue_id,
            or_(SwapRequest.sender_id == user_id, SwapRequest.target_id == user_id)
        )
    )

    await session.flush()
    await reindex_queue(session, queue_id)
    await session.flush()

    queue = await get_queue_for_update(session, queue_id)
    return True, "Вы вышли из очереди", queue


async def atomic_skip_user(
    session: AsyncSession,
    queue_id: int,
    user_id: int
) -> tuple[bool, str, UserModel | None]:
    """Atomically skips the next person in queue."""
    queue = await get_queue_for_update(session, queue_id)
    if not queue:
        return False, "Очередь не существует", None

    current_entry = next((e for e in queue.entries if e.user_id == user_id), None)
    if not current_entry:
        return False, "Вас нет в этой очереди", None

    target_entry = next((e for e in queue.entries if e.position == current_entry.position + 1), None)
    if not target_entry:
        return False, "Вы последний в очереди, некого пропускать", None

    current_entry.position, target_entry.position = target_entry.position, current_entry.position
    await session.flush()
    await reindex_queue(session, queue_id)
    await session.flush()

    return True, "Успешно пропущен", target_entry.user


async def atomic_move_queue(
    session: AsyncSession,
    queue_id: int,
    user_id: int
) -> tuple[bool, str, QueueModel | None]:
    """Moves queue forward: removes the first participant (speaker)."""
    queue = await get_queue_for_update(session, queue_id)
    if not queue:
        return False, "Очередь не существует", None

    if not queue.entries:
        return False, "Очередь пуста", queue

    sorted_entries = sorted(queue.entries, key=lambda x: x.position)
    first_entry = sorted_entries[0]

    room_admins = [m.user_id for m in queue.room.members if m.role == UserRole.ADMIN]
    allowed_users = room_admins + [first_entry.user_id]

    if user_id not in allowed_users:
        return False, "У вас недостаточно прав или сейчас не ваша очередь", queue

    await session.execute(
        delete(QueueEntry).filter_by(id=first_entry.id)
    )
    # Clear swap requests for the removed user
    await session.execute(
        delete(SwapRequest).where(
            SwapRequest.queue_id == queue_id,
            or_(SwapRequest.sender_id == first_entry.user_id, SwapRequest.target_id == first_entry.user_id)
        )
    )

    await session.flush()
    await reindex_queue(session, queue_id)
    await session.flush()

    queue = await get_queue_for_update(session, queue_id)
    return True, "Очередь смещена", queue


async def atomic_clear_queue(
    session: AsyncSession,
    queue_id: int,
    user_id: int
) -> tuple[bool, str]:
    """Clears all entries in queue. Requires room admin rights."""
    queue = await get_queue_for_update(session, queue_id)
    if not queue:
        return False, "Очередь не существует"

    room_admins = [m.user_id for m in queue.room.members if m.role == UserRole.ADMIN]
    if user_id not in room_admins:
        return False, "У вас нет прав для очистки очереди"

    await session.execute(delete(QueueEntry).where(QueueEntry.queue_id == queue_id))
    await session.execute(delete(SwapRequest).where(SwapRequest.queue_id == queue_id))
    queue.current_speaker_id = None
    queue.notified_next_id = None
    await session.flush()
    return True, "Очередь очищена"


async def confirm_and_execute_swap(
    session: AsyncSession, 
    request_id: int, 
    target_id: int
) -> tuple[bool, str, QueueEntry | None, QueueEntry | None, str | None]:
    """Atomically executes swap under queue lock."""
    stmt = (
        select(SwapRequest)
        .options(
            selectinload(SwapRequest.queue),
            selectinload(SwapRequest.sender),
            selectinload(SwapRequest.target)
        )
        .filter(SwapRequest.id == request_id)
    )
    result = await session.execute(stmt)
    request = result.scalar_one_or_none()

    if not request or request.target_id != target_id:
        return False, "Запрос на обмен не найден или предназначен другому пользователю", None, None, None

    # Check expiration
    if request.expires_at and request.expires_at < get_utc_now():
        await session.delete(request)
        await session.flush()
        return False, "Срок действия предложения обмена истек", None, None, None

    # Lock the queue
    queue = await get_queue_for_update(session, request.queue_id)
    if not queue:
        await session.delete(request)
        await session.flush()
        return False, "Очередь не существует", None, None, None

    sender_entry = next((e for e in queue.entries if e.user_id == request.sender_id), None)
    target_entry = next((e for e in queue.entries if e.user_id == request.target_id), None)

    if not sender_entry or not target_entry:
        await session.delete(request)
        await session.flush()
        return False, "Один из участников покинул очередь", None, None, None

    sender_entry.position, target_entry.position = target_entry.position, sender_entry.position
    await session.delete(request)
    await session.flush()
    await reindex_queue(session, queue.id)
    await session.flush()

    return True, "Обмен успешно выполнен", sender_entry, target_entry, queue.name


async def create_swap_request(
    queue_id: int, 
    user_id: int, 
    target_id: int,
    session: AsyncSession | None = None
) -> SwapRequest | None:
    async def _query(s: AsyncSession):
        # Clean expired first
        now = get_utc_now()
        await s.execute(
            delete(SwapRequest).where(
                SwapRequest.queue_id == queue_id,
                SwapRequest.expires_at.isnot(None),
                SwapRequest.expires_at < now
            )
        )

        existing = await s.execute(
            select(SwapRequest).filter(
                SwapRequest.queue_id == queue_id,
                SwapRequest.sender_id == user_id
            )
        )
        if existing.scalar_one_or_none():
            return None

        new_request = SwapRequest(
            queue_id=queue_id,
            sender_id=user_id,
            target_id=target_id,
            created_at=now,
            expires_at=now + timedelta(minutes=20)
        )
        s.add(new_request)
        await s.flush()
        return new_request

    if session is not None:
        return await _query(session)
    async with async_session() as s:
        req = await _query(s)
        await s.commit()
        return req


async def has_active_swap_request(queue_id: int, user_id: int, session: AsyncSession | None = None) -> SwapRequest | None:
    now = get_utc_now()
    stmt = (
        select(SwapRequest)
        .options(
            selectinload(SwapRequest.queue),
            selectinload(SwapRequest.target),
            selectinload(SwapRequest.sender)
        )
        .filter(
            SwapRequest.queue_id == queue_id,
            or_(SwapRequest.sender_id == user_id, SwapRequest.target_id == user_id)
        )
    )

    async def _query(s: AsyncSession):
        result = await s.execute(stmt)
        requests = result.scalars().all()
        for r in requests:
            if r.expires_at and r.expires_at < now:
                await s.delete(r)
                continue
            return r
        await s.flush()
        return None

    if session is not None:
        return await _query(session)
    async with async_session() as s:
        res = await _query(s)
        await s.commit()
        return res


async def get_swap_request_by_id(swap_id: int, session: AsyncSession | None = None) -> SwapRequest | None:
    stmt = select(SwapRequest).filter(SwapRequest.id == swap_id)
    if session is not None:
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
    async with async_session() as s:
        res = await s.execute(stmt)
        return res.scalar_one_or_none()


async def delete_swap_request(swap_id: int, session: AsyncSession | None = None) -> bool:
    if session is not None:
        req = await session.get(SwapRequest, swap_id)
        if req:
            await session.delete(req)
            await session.flush()
            return True
        return False
    async with async_session() as s:
        req = await s.get(SwapRequest, swap_id)
        if req:
            await s.delete(req)
            await s.commit()
            return True
        return False


async def delete_queue(queue_id: int, session: AsyncSession | None = None) -> int | None:
    if session is not None:
        queue = await session.get(QueueModel, queue_id)
        if not queue:
            return None
        room_id = queue.room_id
        await session.delete(queue)
        await session.flush()
        return room_id

    async with async_session() as s:
        queue = await s.get(QueueModel, queue_id)
        if not queue:
            return None
        room_id = queue.room_id
        await s.delete(queue)
        await s.commit()
        return room_id


async def get_entry_by_position(queue_id: int, position: int, user_id: int | None = None, session: AsyncSession | None = None) -> QueueEntry | None:
    stmt = (
        select(QueueEntry)
        .options(selectinload(QueueEntry.user), selectinload(QueueEntry.queue))
        .filter(QueueEntry.queue_id == queue_id, QueueEntry.position == position)
    )
    if user_id:
        stmt = stmt.filter(QueueEntry.user_id != user_id)

    if session is not None:
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
    async with async_session() as s:
        res = await s.execute(stmt)
        return res.scalar_one_or_none()


async def get_entry_by_user_id(queue_id: int, user_id: int, session: AsyncSession | None = None) -> QueueEntry | None:
    stmt = (
        select(QueueEntry)
        .options(selectinload(QueueEntry.queue))
        .filter(QueueEntry.queue_id == queue_id, QueueEntry.user_id == user_id)
    )
    if session is not None:
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
    async with async_session() as s:
        res = await s.execute(stmt)
        return res.scalar_one_or_none()


async def get_entry_with_user_by_user_id(queue_id: int, user_id: int, session: AsyncSession | None = None) -> QueueEntry | None:
    stmt = (
        select(QueueEntry)
        .options(selectinload(QueueEntry.user), selectinload(QueueEntry.queue))
        .filter(QueueEntry.queue_id == queue_id, QueueEntry.user_id == user_id)
    )
    if session is not None:
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
    async with async_session() as s:
        res = await s.execute(stmt)
        return res.scalar_one_or_none()


async def update_msg_and_chat_ids(queue_id: int, message_id: int, chat_id: int, session: AsyncSession | None = None):
    if session is not None:
        queue = await session.get(QueueModel, queue_id)
        if queue:
            queue.last_msg_id = message_id
            queue.last_chat_id = chat_id
            await session.flush()
        return

    async with async_session() as s:
        queue = await s.get(QueueModel, queue_id)
        if queue:
            queue.last_msg_id = message_id
            queue.last_chat_id = chat_id
            await s.commit()


async def clear_msg_and_chat_ids(queue_id: int, session: AsyncSession | None = None):
    if session is not None:
        queue = await session.get(QueueModel, queue_id)
        if queue:
            queue.last_msg_id = None
            queue.last_chat_id = None
            await session.flush()
        return

    async with async_session() as s:
        queue = await s.get(QueueModel, queue_id)
        if queue:
            queue.last_msg_id = None
            queue.last_chat_id = None
            await s.commit()