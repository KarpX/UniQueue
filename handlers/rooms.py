import logging

from aiogram.filters import Command, CommandObject

from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.users import get_or_create_user
from accessors.rooms import (
    bind_room_to_chat,
    delete_room,
    get_room_by_invite_code,
    get_room_by_name_and_creator,
    get_room_member,
    get_rooms_for_user,
    get_room_by_id,
    join_room_with_unicode,
)
from accessors.queues import reindex_queue
from database.session import async_session
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from database.models import QueueEntry, RoomMember, RoomModel, UserRole, QueueModel
from enums import MEMBERS_PER_PAGE, MainMenuButtons
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from handlers.filters.admin_filter import ChatAdminFilter
from main import bot

from states import CreateQueueState, CreateRoomState, JoinRoomState


logger = logging.getLogger(__name__)

router = Router()

@router.message(F.text == MainMenuButtons.CREATE_ROOM.value)
async def create_room_command(message, state: FSMContext):
    await state.set_state(CreateRoomState.waiting_for_room_name)
    return await message.answer(
        "⌨️ Введите название комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )

@router.message(CreateRoomState.waiting_for_room_name)
async def process_room_name(message, state: FSMContext):
    room_name = message.text
    if room_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "❌ Создание комнаты отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    existing_room = await get_room_by_name_and_creator(room_name, user.id)
    if existing_room:
        await message.answer(
            f"⚠️ Комната с названием '{room_name}' уже существует",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        await state.clear()
        return

    # create_room_with_unique_code uses async_session directly
    from accessors.rooms import create_room_with_unique_code  # import here to avoid cycles
    room = await create_room_with_unique_code(room_name, user.id)

    await state.clear()
    return await message.answer(
        f"✅ Комната '{room.name}' успешно создана!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )

@router.message(F.text == MainMenuButtons.USER_ROOMS.value)
async def user_rooms_command(message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    rooms = await get_rooms_for_user(user.id)

    if not rooms:
        await message.answer(
            "🫙 У вас нет комнат\nСоздайте комнату или присоединитесь к существующей",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    return await message.answer(
        f"📂 Ваши комнаты:\n",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms)),
            parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("room:"))
async def room_callback(callback_query: CallbackQuery):
    room_id = int(callback_query.data.split(":")[1])
    room = await get_room_by_id(room_id)
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    async with async_session() as session:
        room_member = await session.get(RoomMember, (user.id, room.id))

    if not room_member:
        await callback_query.answer(
            "🚫 Вы не состоите в этой комнате",
            show_alert=True
        )
        return await callback_query.message.delete()

    bot_info = await bot.get_me()
    invite_link = f"https://t.me/{bot_info.username}?start={room.invite_code}"

    user_role = room_member.role.value
    text = (f"🏫 Комната: <b>{room.name}</b>\n"
            f"👤 Ваша роль: <b>{'Админ' if user_role == UserRole.ADMIN.value else 'Участник'}</b>\n")
    if user_role == UserRole.ADMIN.value:
        text += f"🔑 Код приглашения: <code>{room.invite_code}</code>\n"
        text += f"🔗 Пригласительная ссылка:\n{invite_link}"
    return await callback_query.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilderFactory().room_inline_keyboard(user_role, room_id=room.id),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "back")
async def back_rooms(callback_query: CallbackQuery):
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    rooms = await get_rooms_for_user(user.id)

    if not rooms:
        await callback_query.message.answer(
            "🫙 У вас нет комнат\nСоздайте комнату или присоединитесь к существующей",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    return await callback_query.message.edit_text(
        "📂 Ваши комнаты:",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms))
    )

@router.callback_query(F.data.startswith("leave_room:"))
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
            await callback_query.answer("⚠️ Комната не найдена")
            return

        room_member = next((m for m in room.members if m.user_id == user_id), None)

        if not room_member:
            await callback_query.message.answer("⚠️ Вы не состоите в этой комнате")
            return

        queues_stmt = (
            select(QueueModel.id).filter_by(room_id=room_id)
        )
        queues_result = await session.execute(queues_stmt)
        room_queues_ids = queues_result.scalars().all()

        if room_queues_ids:
            delete_entries_stmt = (
                delete(QueueEntry)
                .where(
                    QueueEntry.user_id == user_id,
                    QueueEntry.queue_id.in_(room_queues_ids)
                )
            )
            await session.execute(delete_entries_stmt)

            await session.flush()

            for q_id in room_queues_ids:
                await reindex_queue(session, q_id)

        if len(room.members) == 1:
            await session.delete(room)
            await session.commit()
            await callback_query.message.delete()
            await callback_query.message.answer(f'🚪 Вы покинули комнату "{room.name}"')
            return

        if room_member.role == UserRole.ADMIN:
            new_admin = next((m for m in room.members if m.user_id != user_id), None)
            if new_admin:
                new_admin.role = UserRole.ADMIN

        await session.delete(room_member)
        await session.commit()

    await callback_query.message.delete()
    return await callback_query.message.answer(f'🚪 Вы покинули комнату "{room.name}"')

@router.callback_query(F.data.startswith("member_back:"))
@router.callback_query(F.data.startswith("room_settings:"))
async def room_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    parts = callback_query.data.split(":")
    action = parts[1]
    room_id = int(parts[2])

    room = await get_room_by_id(room_id)
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    async with async_session() as session:
        room_member = await session.get(RoomMember, (user.id, room.id))

    if not room_member or room_member.role != UserRole.ADMIN:
        await callback_query.answer("🚫 У вас нет прав для выполнения этого действия", show_alert=True)
        return

    if action == "create_queue":
        await state.set_state(CreateQueueState.waiting_for_queue_name)
        await state.update_data(room_id=room_id)
        return await callback_query.message.answer(
            "⌨️ Введите название очереди:",
            reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
        )
    elif action == "settings":
        return await room_settings_handle(callback_query, room_id)
    elif action in ("members", "kick_member"):
        members = await get_room_member(room_id, except_user_id=user.id)
        page = 0
        items_per_page = MEMBERS_PER_PAGE

        start = page * items_per_page
        end = start + items_per_page
        members_to_show = members[start:end]

        return await callback_query.message.edit_text(
            f'👥 Участники комнаты <b>{room.name}</b>:',
            reply_markup=InlineKeyboardBuilderFactory.room_members_keyboard(members_to_show, room_id, 0, len(members), items_per_page),
            parse_mode="HTML"
        )

async def room_settings_handle(callback_query: CallbackQuery, room_id: int):
    room = await get_room_by_id(room_id)
    return await callback_query.message.edit_text(
        f"⚙️ Настройка комнаты <b>{room.name}</b>",
        reply_markup=InlineKeyboardBuilderFactory.room_settings_keyboard(room_id),
        parse_mode="HTML" 
    )

@router.callback_query(F.data.startswith("room_admin_settings:"))
async def room_settings_callback_handler(callback_query: CallbackQuery, state: FSMContext):
    action = callback_query.data.split(":")[1]
    room_id = int(callback_query.data.split(":")[2])
    logger.info(f"ROOM_ID: {room_id}")
    if action == "rename":
        await rename_room(callback_query, room_id, state)
    elif action == "delete":
        await delete_room_handler(callback_query, room_id)

async def delete_room_handler(callback_query: CallbackQuery, room_id: int):
    success = await delete_room(room_id)

    if not success:
        return await callback_query.answer("❌ Ошибка при удалении комнаты", show_alert=True)

    await callback_query.answer("🗑 Комната удалена")
    await callback_query.message.delete()

async def rename_room(callback_query: CallbackQuery, room_id: int, state: FSMContext):
    await state.set_state(CreateRoomState.waiting_for_room_rename)
    await state.update_data(room_id=room_id)
    return await callback_query.message.answer(
        "✏️ Введите новое название комнаты",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )

@router.message(CreateRoomState.waiting_for_room_rename)
async def rename_room_text_handler(message: Message, state: FSMContext):
    room_name = message.text
    room_id = (await state.get_data()).get("room_id")

    if room_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "❌ Переименование комнаты отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    async with async_session() as session:
            result = await session.execute(
                select(RoomModel)
                .filter(RoomModel.id == room_id)
            )
            room = result.scalar_one_or_none()
    
            if not room:
                await message.answer(
                    "⚠️ Комната не существует",
                    reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
                )
                await state.clear()
                return
    
            room.name = room_name
            await session.commit()
    
    await state.clear()
    return await message.answer(
        f"✅ Комната <b>{room.name}</b> успешно переименована!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )

@router.message(F.text == MainMenuButtons.JOIN_ROOM.value)
async def join_room_command(message, state: FSMContext):
    await state.set_state(JoinRoomState.waiting_for_invite_code)
    return await message.answer(
        "🔑 Введите код приглашения комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )

@router.message(JoinRoomState.waiting_for_invite_code)
async def process_invite_code(message, state: FSMContext):
    invite_code = message.text
    if invite_code == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "❌ Присоединение к комнате отменено",
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
                f"🔍 Комната с кодом '{invite_code}' не найдена",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        user = await get_or_create_user(message.from_user.id, message.from_user.username)

        existing_member = await session.get(RoomMember, (user.id, room.id))
        if existing_member:
            await message.answer(
                f"ℹ️ Вы уже состоите в комнате <b>{room.name}</b>",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
                parse_mode="HTML"
            )
            await state.clear()
            return

        room_member = RoomMember(user_id=user.id, room_id=room.id, role=UserRole.MEMBER)
        session.add(room_member)
        await session.commit()

    await state.clear()
    return await message.answer(
        f"✅ Вы успешно присоединились к комнате <b>{room.name}</b>!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("queue:"))
async def queue_callback(callback_query: CallbackQuery, room_id: int | None = None):
    if not room_id:
        room_id = int(callback_query.data.split(":")[1])
    
    user_id = callback_query.from_user.id
    async with async_session() as session:
        result = await session.execute(
            select(RoomModel)
            .options(selectinload(RoomModel.members))
            .filter(RoomModel.id == room_id)
        )
        room = result.scalar_one_or_none()

        queue_res = await session.execute(
            select(QueueModel).filter(QueueModel.room_id == room_id)
        )
        queues = queue_res.scalars().all()

    room_users_ids = [member.user_id for member in room.members]
    if not room:
        return await callback_query.answer(
            "⚠️ Такая комната не существует",
            show_alert=True
        )

    if user_id and user_id not in room_users_ids:
        return await callback_query.answer(
            "🚫 Вы не участник комнаты",
            show_alert=True
        )

    buttons = [(queue.name, f"open_queue:{queue.id}") for queue in queues]
    buttons += [("⬅️ Назад", f"room:{room.id}")]
    
    return await callback_query.message.edit_text(
        f"📋 Очереди в комнате <b>{room.name}</b>",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            buttons, 
            adjust=[2] * len(queues),),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("mem:list:"))
async def process_member_list(callback_query: CallbackQuery):
    _, _, room_id, _, page = callback_query.data.split(":")
    room_id = int(room_id)
    page = int(page)

    room = await get_room_by_id(room_id)
    members = await get_room_member(room_id)
    items_per_page = MEMBERS_PER_PAGE

    start = page * items_per_page
    end = start + items_per_page
    members_to_show = members[start:end]

    return await callback_query.message.edit_text(
        f'👥 Участники комнаты <b>{room.name}</b>:',
        reply_markup=InlineKeyboardBuilderFactory.room_members_keyboard(members_to_show, room_id, page, len(members), items_per_page),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("room_view:"))
async def process_room_view(callback_query: CallbackQuery):
    return await room_callback(callback_query)

async def join_room_by_link(message: Message, user_id: int, invite_code: str):
    user = await get_or_create_user(user_id)
    if not user:
        return await message.answer("👤 Пользователь не найден")

    result, room = await join_room_with_unicode(user.id, invite_code)

    if not room:
        if result == "not_found":
            return await message.answer(
                "🔍 Комната не найдена",
                reply_markup=ReplyKeyboardBuilderFactory.create_main_menu_keyboard()
            )
        elif result == "already_in":
            return await message.answer(
                "ℹ️ Вы уже состоите в этой комнате",
                reply_markup=ReplyKeyboardBuilderFactory.create_main_menu_keyboard()
            )

    return await message.answer(
        f"✅ Вы успешно присоединились к комнате <b>{room.name}</b>",
        reply_markup=ReplyKeyboardBuilderFactory.create_main_menu_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("bind"), F.chat.type.in_({"group", "supergroup"}), ChatAdminFilter())
async def cmd_bind(message: Message, command: CommandObject):
    if not command.args:
        return await message.answer("⌨️ Использование: /bind [код комнаты]")

    invite_code = command.args.strip().upper()
    user_id = message.from_user.id
    chat_id = message.chat.id

    room = await get_room_by_invite_code(invite_code)

    if not room or room.creator_id != user_id:
        return await message.answer("🚫 Вы должны быть создателем комнаты")

    room = await bind_room_to_chat(room.id, chat_id)
    await message.answer(f"🔗 Группа успешно привязана к комнате <b>{room.name}</b>", parse_mode="HTML")

@router.message(Command("unbind"), F.chat.type.in_({"group", "supergroup"}), ChatAdminFilter())
async def cmd_unbind(message: Message, command: CommandObject):
    user_id = message.from_user.id
    chat_id = message.chat.id

    async with async_session() as session:
        result = await session.execute(
            select(RoomModel).filter_by(telegram_chat_id=chat_id)
        )
        room = result.scalar_one_or_none()

        if not room or room.creator_id != user_id:
            return await message.answer("🚫 Вы должны быть создателем комнаты")

        room.telegram_chat_id = None
        await session.commit()

    await message.answer(f"🔗 Группа успешно отвязана от комнаты <b>{room.name}</b>", parse_mode="HTML")

# End of rooms handlers
