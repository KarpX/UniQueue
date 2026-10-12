import asyncio
import logging
from datetime import datetime, timezone
from aiogram import Bot
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from database.models import SwapRequest, QueueModel
from database.session import async_session
from utils import safe_html

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL_SECONDS = 120  # Runs every 2 minutes


async def cleanup_expired_swap_requests(bot: Bot | None = None) -> int:
    """
    Deletes all expired swap requests from the database and optionally
    notifies the sender that their swap proposal has expired.
    """
    now = datetime.now(timezone.utc)
    deleted_count = 0

    try:
        async with async_session() as session:
            stmt = (
                select(SwapRequest)
                .options(selectinload(SwapRequest.queue))
                .filter(SwapRequest.expires_at.isnot(None), SwapRequest.expires_at < now)
            )
            result = await session.execute(stmt)
            expired_requests = result.scalars().all()

            if not expired_requests:
                return 0

            for req in expired_requests:
                queue_name = req.queue.name if req.queue else "очереди"
                sender_id = req.sender_id

                # Notify sender if bot instance is provided
                if bot and sender_id:
                    try:
                        await bot.send_message(
                            chat_id=sender_id,
                            text=f"⌛ Срок действия предложения обмена в очереди <b>{safe_html(queue_name)}</b> истек (20 минут без ответа). Запрос отменен.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.debug(f"Could not notify user {sender_id} about expired swap: {e}")

                await session.delete(req)
                deleted_count += 1

            await session.commit()
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired swap requests.")

    except Exception as e:
        logger.error(f"Error during expired swap requests cleanup: {e}")

    return deleted_count


async def run_periodic_swap_cleanup(bot: Bot):
    """Background worker that periodically runs cleanup_expired_swap_requests."""
    logger.info("Starting background periodic swap cleanup task...")
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            await cleanup_expired_swap_requests(bot=bot)
        except asyncio.CancelledError:
            logger.info("Swap cleanup background task cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in run_periodic_swap_cleanup: {e}")
            await asyncio.sleep(10)
