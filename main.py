import asyncio
import logging
import os

from aiogram import F, Bot, Dispatcher
from dotenv import load_dotenv
from redis import Redis
from aiogram.fsm.storage.redis import RedisStorage

import handlers.users as handlers_users
import handlers.rooms as handlers_rooms
import handlers.queues as handlers_queues


load_dotenv()

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_HOST = os.getenv("REDIS_HOST")

bot = Bot(token=BOT_TOKEN)

redis_client = Redis(host=REDIS_HOST)
storage = RedisStorage(redis=redis_client)
dp = Dispatcher(storage=storage)


async def main():
    dp.include_routers(
        handlers_rooms.router,
        handlers_queues.router,
        handlers_users.router
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())