import asyncio
import logging
import time
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.session import async_session
from database.models import QueueModel, QueueEntry, RoomModel, UserRole
from builders import InlineKeyboardBuilderFactory
from utils import get_user_display_name, safe_html

logger = logging.getLogger(__name__)

_pending_updates: dict[int, asyncio.Task] = {}
_last_update_ts: dict[int, float] = {}
_MIN_INTERVAL = 1.2  # Telegram rate limit safety window (seconds)


async def generate_queue_message_data(
    queue: QueueModel, 
    user_id: int = 0, 
    in_group: bool = False,
    bot_username: str | None = None
) -> tuple[str, any]:
    """Renders text and keyboard for a queue."""
    room = queue.room
    room_members_admin_ids = [m.user_id for m in room.members if m.role == UserRole.ADMIN] if room else []

    text_lines = [f"📋 Очередь <b>{safe_html(queue.name)}</b>", ""]
    user_ids_in_queue = [entry.user_id for entry in queue.entries]

    if not queue.entries:
        text_lines.append("🫙 <i>Пусто...</i>")
    else:
        sorted_entries = sorted(queue.entries, key=lambda x: (x.position, x.id))
        # Limit visible list if too long to prevent hitting 4096 char Telegram limit
        max_displayed = 50
        for entry in sorted_entries[:max_displayed]:
            user_label = get_user_display_name(entry.user)
            text_lines.append(f"{entry.position}. {user_label}")
        
        if len(sorted_entries) > max_displayed:
            text_lines.append(f"\n<i>...и еще {len(sorted_entries) - max_displayed} участников</i>")

    is_in_queue = user_id in user_ids_in_queue

    keyboard = InlineKeyboardBuilderFactory.queue_inline_keyboard(
        user_id=user_id,
        is_in_queue=is_in_queue,
        room_members_admin_ids=room_members_admin_ids,
        queue=queue,
        in_group=in_group,
        bot_username=bot_username
    )

    return "\n".join(text_lines), keyboard


async def _perform_update(bot: Bot, queue_id: int):
    try:
        async with async_session() as session:
            stmt = (
                select(QueueModel)
                .options(
                    selectinload(QueueModel.entries).selectinload(QueueEntry.user),
                    selectinload(QueueModel.room).selectinload(RoomModel.members)
                )
                .filter(QueueModel.id == queue_id)
            )
            result = await session.execute(stmt)
            queue = result.scalar_one_or_none()

            if not queue or not queue.last_msg_id or not queue.last_chat_id:
                return

            bot_info = await bot.get_me()
            text, keyboard = await generate_queue_message_data(
                queue=queue,
                user_id=0,
                in_group=True,
                bot_username=bot_info.username
            )

            try:
                await bot.edit_message_text(
                    chat_id=queue.last_chat_id,
                    message_id=queue.last_msg_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except TelegramBadRequest as e:
                err_text = str(e).lower()
                if "message is not modified" in err_text:
                    pass
                elif "message to edit not found" in err_text or "chat not found" in err_text:
                    queue.last_msg_id = None
                    queue.last_chat_id = None
                    await session.commit()
                else:
                    logger.warning(f"TelegramBadRequest updating queue {queue_id}: {e}")
            except TelegramRetryAfter as e:
                logger.warning(f"Flood control for queue {queue_id}. Retry after {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
                # Reschedule update after retry timeout
                schedule_live_queue_update(bot, queue_id)
            except Exception as e:
                logger.error(f"Failed to edit queue message {queue_id}: {e}")

    finally:
        _last_update_ts[queue_id] = time.monotonic()
        _pending_updates.pop(queue_id, None)


def schedule_live_queue_update(bot: Bot, queue_id: int):
    """
    Schedules an update of the live queue message in the group with debouncing
    to prevent Telegram rate limits and duplicate edits.
    """
    if queue_id in _pending_updates and not _pending_updates[queue_id].done():
        # A task is already scheduled/running and will fetch the latest state
        return

    now = time.monotonic()
    last_ts = _last_update_ts.get(queue_id, 0.0)
    elapsed = now - last_ts
    delay = max(0.0, _MIN_INTERVAL - elapsed)

    async def _debounced():
        if delay > 0:
            await asyncio.sleep(delay)
        await _perform_update(bot, queue_id)

    _pending_updates[queue_id] = asyncio.create_task(_debounced())
