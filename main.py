import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from dotenv import load_dotenv
from redis.asyncio import Redis

import handlers.users as handlers_users
import handlers.rooms as handlers_rooms
import handlers.queues as handlers_queues
from database.session import async_session
from middlewares.db import DbSessionMiddleware
from services.swap_cleanup import run_periodic_swap_cleanup

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables!")

bot = Bot(token=BOT_TOKEN)

if REDIS_HOST:
    redis_client = Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)
    storage = RedisStorage(redis=redis_client)
    logger.info(f"Using Redis storage at {REDIS_HOST}:{REDIS_PORT}")
else:
    storage = MemoryStorage()
    logger.info("Using in-memory FSM storage")

dp = Dispatcher(storage=storage)


async def main():
    # Register DB session middleware for all updates
    dp.update.middleware(DbSessionMiddleware(session_pool=async_session))

    # Register routers
    dp.include_routers(
        handlers_rooms.router,
        handlers_queues.router,
        handlers_users.router
    )

    # Cache bot info on startup
    bot_info = await bot.get_me()
    dp["bot_username"] = bot_info.username
    logger.info(f"Starting bot @{bot_info.username}")
    # Drop old pending updates to prevent event floods on restart
    await bot.delete_webhook(drop_pending_updates=True)

    # Start background worker for periodic swap requests cleanup
    asyncio.create_task(run_periodic_swap_cleanup(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())