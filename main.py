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
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from enums import MainMenuButtons, QueueInlineButtons

# Accessors (DB access)
from accessors.users import get_or_create_user
from accessors.rooms import get_room_by_name_and_creator, get_room_by_id
from accessors.queues import get_queue_with_data, reindex_queue

# Handlers (business logic / request processing)
import handlers.users as handlers_users
import handlers.rooms as handlers_rooms
import handlers.queues as handlers_queues


load_dotenv()

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


class CreateRoomState(StatesGroup):
    waiting_for_room_name = State()


class JoinRoomState(StatesGroup):
    waiting_for_invite_code = State()


class CreateQueueState(StatesGroup):
    waiting_for_queue_name = State()


# get_or_create_user is provided by accessors.users.get_or_create_user


@dp.message(Command("start"))
async def start_command(message):
    await handlers_users.start_command(message)


# ----------------- Create Room Command -----------------

@dp.message(F.text == MainMenuButtons.CREATE_ROOM.value)
async def create_room_command(message, state: FSMContext):
    await handlers_rooms.create_room_command(message, state)


@dp.message(CreateRoomState.waiting_for_room_name)
async def process_room_name(message, state: FSMContext):
    await handlers_rooms.process_room_name(message, state)


# ----------------- My Rooms Command -----------------

@dp.message(F.text == MainMenuButtons.USER_ROOMS.value)
async def user_rooms_command(message):
    await handlers_rooms.user_rooms_command(message)


@dp.callback_query(F.data.startswith("room:"))
async def room_callback(callback_query):
    await handlers_rooms.room_callback(callback_query)


@dp.callback_query(F.data == "back")
async def back_rooms(callback_query):
    await handlers_rooms.back_rooms(callback_query)


@dp.callback_query(F.data.startswith("leave_room:"))
async def leave_room(callback_query: CallbackQuery):
    await handlers_rooms.leave_room(callback_query)


@dp.callback_query(F.data.startswith("room_settings:"))
async def room_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    await handlers_rooms.room_settings_callback(callback_query, state)


@dp.callback_query(F.data.startswith("queue:"))
async def queue_callback(callback_query: CallbackQuery):
    await handlers_rooms.queue_callback(callback_query)


@dp.message(CreateQueueState.waiting_for_queue_name)
async def process_queue_name(message, state: FSMContext):
    await handlers_queues.process_queue_name(message, state)

# -------------- Join Room Command --------------

@dp.message(F.text == MainMenuButtons.JOIN_ROOM.value)
async def join_room_command(message, state: FSMContext):
    await handlers_rooms.join_room_command(message, state)


@dp.message(JoinRoomState.waiting_for_invite_code)
async def process_invite_code(message, state: FSMContext):
    await handlers_rooms.process_invite_code(message, state)

# ----------------- Queue Functions ------------------

# get_queue_with_data is implemented in accessors.queues.get_queue_with_data


async def generate_queue_message(user_id: int, queue: QueueModel):
    # delegate to handlers.queues.generate_queue_message
    return await handlers_queues.generate_queue_message(user_id, queue)


# reindex_queue is implemented in accessors.queues.reindex_queue


@dp.callback_query(F.data.startswith("open_queue:"))
async def open_queue_callback(callback_query: CallbackQuery):
    await handlers_queues.open_queue_callback(callback_query)


@dp.callback_query(F.data.startswith("queue_control:"))
async def queue_control_handler(callback_query: CallbackQuery):
    await handlers_queues.queue_control_handler(callback_query)

# ----------------- Helper Functions -----------------


async def get_room_by_name_and_creator(room_name: str, user_id: int) -> RoomModel | None:
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).filter_by(name=room_name, creator_id=user_id)
        )
        return result.scalar_one_or_none()


async def create_room_with_unique_code(session, room_name: str, creator_id: int) -> RoomModel:
    while True:
        invite_code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        existing_room = await session.execute(
            select(RoomModel).filter_by(invite_code=invite_code)
        )
        if not existing_room.scalar():
            break

    room = RoomModel(name=room_name, invite_code=invite_code, creator_id=creator_id)
    room_member = RoomMember(user_id=creator_id, room=room, role=UserRole.ADMIN)
    session.add(room)
    session.add(room_member)
    await session.commit()
    return room


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())