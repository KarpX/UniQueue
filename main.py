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


async def get_or_create_user(user_id: int, username: str | None = None) -> UserModel:
    async with async_session() as session:
        user = await session.get(UserModel, user_id)
        if not user:
            user = UserModel(id=user_id, username=username)
            session.add(user)
            await session.commit()
        return user


@dp.message(Command("start"))
async def start_command(message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"Hello, @{user.username or 'User'}! Your ID is {user.id}.",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )


# ----------------- Create Room Command -----------------

@dp.message(F.text == MainMenuButtons.CREATE_ROOM.value)
async def create_room_command(message, state: FSMContext):
    await state.set_state(CreateRoomState.waiting_for_room_name)
    await message.answer(
        "Введите название комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


@dp.message(CreateRoomState.waiting_for_room_name)
async def process_room_name(message, state: FSMContext):
    room_name = message.text
    if room_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "Создание комнаты отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    existing_room = await get_room_by_name_and_creator(room_name, user.id)
    if existing_room:
        await message.answer(
            f"Комната с названием '{room_name}' уже существует",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        await state.clear()
        return

    room = await create_room_with_unique_code(async_session(), room_name, user.id)

    await state.clear()
    await message.answer(
        f"Комната '{room.name}' успешно создана!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )


# ----------------- My Rooms Command -----------------

@dp.message(F.text == MainMenuButtons.USER_ROOMS.value)
async def user_rooms_command(message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).join(RoomModel.members).filter_by(user_id=user.id)
        )
        rooms = result.scalars().all()

    if not rooms:
        await message.answer(
            "У вас нет комнат\nСоздайте комнату или присоединитесь к существующей",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    room_list = "\n".join([f"{room.name} (Invite Code: <code>{room.invite_code}</code>)" for room in rooms])
    await message.answer(
        f"Ваши комнаты:\n",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms)),
            parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("room:"))
async def room_callback(callback_query):
    room_id = int(callback_query.data.split(":")[1])
    async with async_session() as session:
        room = await session.get(RoomModel, room_id)
        user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
        room_member = await session.get(RoomMember, (user.id, room.id))

    if not room_member:
        await callback_query.message.answer(
            "Вы не состоите в этой комнате",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    user_role = room_member.role.value
    text = (f"Комната: <b>{room.name}</b>\n"
            f"Ваша роль: <b>{'Админ' if user_role == UserRole.ADMIN.value else 'Участник'}</b>\n")
    if user_role == UserRole.ADMIN.value:
        text += f"Код приглашения: <code>{room.invite_code}</code>"
    await callback_query.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilderFactory().room_inline_keyboard(user_role, room_id=room.id),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "back")
async def back_rooms(callback_query):
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).join(RoomModel.members).filter_by(user_id=user.id)
        )
        rooms = result.scalars().all()

    if not rooms:
        await callback_query.message.answer(
            "У вас нет комнат\nСоздайте комнату или присоединитесь к существующей",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    await callback_query.message.edit_text(
        "Ваши комнаты:",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms))
    )


@dp.callback_query(F.data.startswith("leave_room:"))
async def leave_room(callback_query: CallbackQuery):
    room_id = int(callback_query.data.split(":")[1])
    user_id = callback_query.from_user.id

    async with async_session() as session:
        stmt = (
            select(RoomModel)
            .options(selectinload(RoomModel.members))
            .filter(RoomModel.id == room_id)
        )
        result = await session.execute(stmt)
        room = result.scalar_one_or_none()

        if not room:
            await callback_query.answer("Комната не найдена")
            return

        room_member = next((m for m in room.members if m.user_id == user_id), None)

        if not room_member:
            await callback_query.message.answer("Вы не состоите в этой комнате")
            return

        if len(room.members) == 1:
            await session.delete(room)
            await session.commit()
            await callback_query.message.delete()
            await callback_query.message.answer(f'Вы покинули комнату "{room.name}"')
            return

        if room_member.role == UserRole.ADMIN:
            new_admin = next((m for m in room.members if m.user_id != user_id), None)
            if new_admin:
                new_admin.role = UserRole.ADMIN


        await session.delete(room_member)
        await session.commit()

    await callback_query.message.delete()
    await callback_query.message.answer(f'Вы покинули комнату "{room.name}"')


@dp.callback_query(F.data.startswith("room_settings:"))
async def room_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    parts = callback_query.data.split(":")
    action = parts[1]
    room_id = int(parts[2])

    async with async_session() as session:
        room = await session.get(RoomModel, room_id)
        user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
        room_member = await session.get(RoomMember, (user.id, room.id))

    if not room_member or room_member.role != UserRole.ADMIN:
        await callback_query.answer("У вас нет прав для выполнения этого действия", show_alert=True)
        return

    if action == "create_queue":
        await state.set_state(CreateQueueState.waiting_for_queue_name)
        await state.update_data(room_id=room_id)
        await callback_query.message.answer(
            "Введите название очереди:",
            reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
        )
    elif action == "settings":
        await callback_query.message.answer(f"Настройки комнаты '{room.name}' (функционал не реализован)")
    elif action == "members":
        async with async_session() as session:
            result = await session.execute(
                select(UserModel).join(RoomMember).filter(RoomMember.room_id == room.id)
            )
            members = result.scalars().all()
            member_list = "\n".join([f"- @{member.username or 'User'} (ID: {member.id})" for member in members])
            await callback_query.message.answer(
                f"Участники комнаты '{room.name}':\n{member_list}"
            )


@dp.callback_query(F.data.startswith("queue:"))
async def queue_callback(callback_query: CallbackQuery):
    room_id = int(callback_query.data.split(":")[1])
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).filter(RoomModel.id == room_id)
        )
        room = result.scalar_one_or_none()

        queue_res = await session.execute(
            select(QueueModel).filter(QueueModel.room_id == room_id)
        )
        queues = queue_res.scalars().all()

    if not room:
        return await callback_query.message.answer(
            "Такая комната не существует"
        )

    buttons = [(queue.name, f"open_queue:{queue.id}") for queue in queues]
    buttons += [("Назад", f"room:{room.id}")]
    
    await callback_query.message.edit_text(
        f"Очереди в комнате <b>{room.name}</b>",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            buttons, 
            adjust=[2] * len(queues),),
        parse_mode="HTML"
    )


@dp.message(CreateQueueState.waiting_for_queue_name)
async def process_queue_name(message, state: FSMContext):
    queue_name = message.text
    room_id = (await state.get_data()).get("room_id")
    logger.info(f"User {message.from_user.id} is creating queue '{queue_name}' in room {room_id}")
    if queue_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "Создание очереди отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    async with async_session() as session:
        user = await get_or_create_user(message.from_user.id, message.from_user.username)
        result = await session.execute(
            select(RoomMember).filter_by(user_id=user.id, room_id=room_id, role=UserRole.ADMIN)
        )
        admin_room = result.scalars().all()

        if not admin_room:
            await message.answer(
                "Вы не являетесь администратором комнаты",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        queue = QueueModel(name=queue_name, room_id=room_id)
        session.add(queue)
        await session.commit()

    await state.clear()
    await message.answer(
        f"Очередь '{queue.name}' успешно создана в комнате с ID {room_id}!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )

# -------------- Join Room Command --------------

@dp.message(F.text == MainMenuButtons.JOIN_ROOM.value)
async def join_room_command(message, state: FSMContext):
    await state.set_state(JoinRoomState.waiting_for_invite_code)
    await message.answer(
        "Введите код приглашения комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


@dp.message(JoinRoomState.waiting_for_invite_code)
async def process_invite_code(message, state: FSMContext):
    invite_code = message.text
    if invite_code == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "Присоединение к комнате отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).filter_by(invite_code=invite_code)
        )
        room = result.scalar_one_or_none()

        if not room:
            await message.answer(
                f"Комната с кодом '{invite_code}' не найдена",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        user = await get_or_create_user(message.from_user.id, message.from_user.username)

        existing_member = await session.get(RoomMember, (user.id, room.id))
        if existing_member:
            await message.answer(
                f"Вы уже состоите в комнате '{room.name}'",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        room_member = RoomMember(user_id=user.id, room_id=room.id, role=UserRole.MEMBER)
        session.add(room_member)
        await session.commit()

    await state.clear()
    await message.answer(
        f"Вы успешно присоединились к комнате '{room.name}'!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )

# ----------------- Queue Functions ------------------

async def get_queue_with_data(session, queue_id: int):
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


async def generate_queue_message(user_id: int, queue: QueueModel):
    """Генерирует текст и кнопки для сообщения очереди"""
    text_lines = [f"Очередь <b>{queue.name}</b>", ""]
    
    user_ids_in_queue = [entry.user_id for entry in queue.entries]
    
    if not queue.entries:
        text_lines.append("<i>Пусто...</i>")
    else:
        sorted_entries = sorted(queue.entries, key=lambda x: x.position)
        for entry in sorted_entries:
            user_label = entry.user.username or entry.user.id
            text_lines.append(f"{entry.position}. @{user_label}")

    is_in_queue = user_id in user_ids_in_queue
    
    buttons = []
    if not is_in_queue:
        buttons.append((QueueInlineButtons.JOIN_QUEUE.value, f"queue_control:join:{queue.id}"))
    else:
        buttons.append((QueueInlineButtons.EXIT_QUEUE.value, f"queue_control:exit:{queue.id}"))

    buttons.extend([
        (QueueInlineButtons.SKIP_QUEUE.value, f"queue_control:skip:{queue.id}"),
        (QueueInlineButtons.BACK_QUEUE.value, f"queue:{queue.room_id}")
    ])

    return "\n".join(text_lines), buttons


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


@dp.callback_query(F.data.startswith("open_queue:"))
async def open_queue_callback(callback_query: CallbackQuery):
    queue_id = int(callback_query.data.split(":")[1])
    
    async with async_session() as session:
        queue = await get_queue_with_data(session, queue_id)

    if not queue:
        return await callback_query.answer("Очередь не найдена")

    text, buttons = await generate_queue_message(callback_query.from_user.id, queue)
    
    await callback_query.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[1, 1, 1]),
        parse_mode="HTML"
    )
    await callback_query.answer()


@dp.callback_query(F.data.startswith("queue_control:"))
async def queue_control_handler(callback_query: CallbackQuery):
    data = callback_query.data.split(":")
    command, queue_id = data[1], int(data[2])
    user_id = callback_query.from_user.id

    async with async_session() as session:
        queue = await get_queue_with_data(session, queue_id)
        if not queue:
            return await callback_query.answer("Очередь не существует")

        stmt_member = (
            select(RoomMember)
            .filter_by(user_id=user_id, room_id=queue.room_id)
        )
        result_member = await session.execute(stmt_member)
        membership = result_member.scalar_one_or_none()

        if not membership:
            return await callback_query.answer(
                "❌ Вы не состоите в этой комнате! Сначала вступите в группу.", 
                show_alert=True
            )

        
        user = await session.get(UserModel, user_id)
        if not user:
            user = UserModel(id=user_id, username=callback_query.from_user.username)
            session.add(user)
            await session.flush()

        is_already_in = any(e.user_id == user_id for e in queue.entries)

        if command == "join":
            if is_already_in:
                return await callback_query.answer("Вы уже записаны!")
            
            new_entry = QueueEntry(
                queue_id=queue_id, 
                user_id=user_id, 
                position=len(queue.entries) + 1
            )
            session.add(new_entry)
            await callback_query.answer("Вы записались в очередь")

        elif command == "exit":
            if not is_already_in:
                return await callback_query.answer("Вас нет в этой очереди")
            
            await session.execute(
                delete(QueueEntry).filter_by(queue_id=queue_id, user_id=user_id)
            )

            await session.flush()

            await reindex_queue(session, queue_id)
            
            await session.commit()
            
            await callback_query.answer("Вы вышли из очереди")

        await session.commit()
        
        updated_queue = await get_queue_with_data(session, queue_id)

    text, buttons = await generate_queue_message(user_id, updated_queue)
    try:
        await callback_query.message.edit_text(
            text,
            reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[1, 1, 1]),
            parse_mode="HTML"
        )
    except Exception:
        logger.info("Ошибка при изменении сообщения очереди")
        pass

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