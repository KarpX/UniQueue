import asyncio
import logging
import os
import secrets
import string

from aiogram import F, Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from dotenv import load_dotenv
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from database.models import QueueEntry, QueueModel, RoomMember, RoomModel, UserModel, UserRole
from database.session import async_session
from aiogram.fsm.context import FSMContext
from enums import MainMenuButtons, QueueInlineButtons

# Handlers (business logic / request processing)
import handlers.users as handlers_users
import handlers.rooms as handlers_rooms
import handlers.queues as handlers_queues

from states import CreateQueueState, CreateRoomState, JoinRoomState


load_dotenv()

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


async def main():
    dp.include_routers(
        handlers_rooms.router,
        handlers_queues.router,
        handlers_users.router
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())