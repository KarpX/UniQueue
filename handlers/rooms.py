import logging
from aiogram import F, Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from accessors.queues import reindex_queue
from accessors.rooms import (
    bind_room_to_chat,
    create_room_with_unique_code,
    delete_room,
    get_room_by_id,
    get_room_by_invite_code,
    get_room_by_name_and_creator,
    get_room_member,
    get_rooms_for_user,
    join_room_with_unicode,
)
from accessors.users import get_or_create_user
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from database.models import QueueEntry, QueueModel, RoomMember, RoomModel, UserRole
from enums import MEMBERS_PER_PAGE, MainMenuButtons
from handlers.filters.admin_filter import ChatAdminFilter
from services.queue_updater import schedule_live_queue_update
from states import CreateQueueState, CreateRoomState, JoinRoomState
from utils import safe_html

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text == MainMenuButtons.CREATE_ROOM.value)
async def create_room_command(message: Message, state: FSMContext):
    await state.set_state(CreateRoomState.waiting_for_room_name)
    return await message.answer(
        "⌨️ Введите название комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


@router.message(CreateRoomState.waiting_for_room_name)
async def process_room_name(message: Message, state: FSMContext, session: AsyncSession):
    room_name = message.text
    if not room_name or room_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Создание комнаты отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    room_name = room_name.strip()
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        session=session
    )

    existing_room = await get_room_by_name_and_creator(room_name, user.id, session=session)
    if existing_room:
        await state.clear()
        return await message.answer(
            f"⚠️ Комната с названием «{safe_html(room_name)}» уже существует",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
            parse_mode="HTML"
        )

    room = await create_room_with_unique_code(room_name, user.id, session=session)
    await session.commit()
    await state.clear()

    return await message.answer(
        f"✅ Комната <b>{safe_html(room.name)}</b> успешно создана!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == MainMenuButtons.USER_ROOMS.value)
async def user_rooms_command(message: Message, session: AsyncSession):
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        session=session
    )
    rooms = await get_rooms_for_user(user.id, session=session)

    if not rooms:
        return await message.answer(
            "🫙 У вас нет комнат\nСоздайте комнату или присоединитесь к существующей",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    return await message.answer(
        "📂 Ваши комнаты:\n",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms)
        ),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("room:"))
async def room_callback(callback_query: CallbackQuery, bot: Bot, session: AsyncSession):
    room_id = int(callback_query.data.split(":")[1])
    room = await get_room_by_id(room_id, session=session)
    if not room:
        await callback_query.answer("⚠️ Комната не найдена", show_alert=True)
        return await callback_query.message.delete()

    user = await get_or_create_user(
        user_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
        last_name=callback_query.from_user.last_name,
        session=session
    )
    room_member = await session.get(RoomMember, (user.id, room.id))

    if not room_member:
        await callback_query.answer("🚫 Вы не состоите в этой комнате", show_alert=True)
        return await callback_query.message.delete()

    bot_info = await bot.get_me()
    invite_link = f"https://t.me/{bot_info.username}?start={room.invite_code}"

    user_role = room_member.role.value
    text = (
        f"🏫 Комната: <b>{safe_html(room.name)}</b>\n"
        f"👤 Ваша роль: <b>{'Администратор 👑' if user_role == UserRole.ADMIN.value else 'Участник 👤'}</b>\n"
    )
    if user_role == UserRole.ADMIN.value:
        text += (
            f"🔑 Код приглашения: <code>{safe_html(room.invite_code)}</code>\n"
            f"🔗 Пригласительная ссылка:\n{invite_link}"
        )

    return await callback_query.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilderFactory().room_inline_keyboard(user_role, room_id=room.id),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "back")
async def back_rooms(callback_query: CallbackQuery, session: AsyncSession):
    user = await get_or_create_user(
        user_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
        last_name=callback_query.from_user.last_name,
        session=session
    )
    rooms = await get_rooms_for_user(user.id, session=session)

    if not rooms:
        return await callback_query.message.edit_text(
            "🫙 У вас нет комнат\nСоздайте комнату или присоединитесь к существующей"
        )

    return await callback_query.message.edit_text(
        "📂 Ваши комнаты:",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms)
        )
    )


@router.callback_query(F.data.startswith("leave_room:"))
async def leave_room(callback_query: CallbackQuery, bot: Bot, session: AsyncSession):
    room_id = int(callback_query.data.split(":")[1])
    user_id = callback_query.from_user.id

    stmt = (
        select(RoomModel)
        .options(selectinload(RoomModel.members))
        .filter(RoomModel.id == room_id)
    )
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()

    if not room:
        return await callback_query.answer("⚠️ Комната не найдена", show_alert=True)

    room_member = next((m for m in room.members if m.user_id == user_id), None)
    if not room_member:
        return await callback_query.answer("⚠️ Вы не состоите в этой комнате", show_alert=True)

    # 1. Clean up queue entries for this user in all room queues
    queues_result = await session.execute(select(QueueModel.id).filter_by(room_id=room_id))
    room_queues_ids = queues_result.scalars().all()

    if room_queues_ids:
        await session.execute(
            delete(QueueEntry).where(
                QueueEntry.user_id == user_id,
                QueueEntry.queue_id.in_(room_queues_ids)
            )
        )
        await session.flush()
        for q_id in room_queues_ids:
            await reindex_queue(session, q_id)
            schedule_live_queue_update(bot, q_id)

    # 2. Check if user is the last member
    if len(room.members) == 1:
        await session.delete(room)
        await session.commit()
        await callback_query.message.delete()
        return await callback_query.message.answer(f'🚪 Вы покинули комнату «{safe_html(room.name)}». Она была удалена, так как в ней не осталось участников.')

    # 3. If leaving user is creator or admin, transfer leadership
    other_members = [m for m in room.members if m.user_id != user_id]
    if room.creator_id == user_id:
        # Promote another admin or first member to creator
        new_leader = next((m for m in other_members if m.role == UserRole.ADMIN), other_members[0])
        new_leader.role = UserRole.ADMIN
        room.creator_id = new_leader.user_id
    elif room_member.role == UserRole.ADMIN:
        has_other_admin = any(m.role == UserRole.ADMIN for m in other_members)
        if not has_other_admin:
            other_members[0].role = UserRole.ADMIN

    await session.delete(room_member)
    await session.commit()

    await callback_query.message.delete()
    return await callback_query.message.answer(f'🚪 Вы покинули комнату «{safe_html(room.name)}»')


@router.callback_query(F.data.startswith("member_back:"))
@router.callback_query(F.data.startswith("room_settings:"))
async def room_settings_callback(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback_query.data.split(":")
    action = parts[1]
    room_id = int(parts[2])

    room = await get_room_by_id(room_id, session=session)
    if not room:
        return await callback_query.answer("⚠️ Комната не найдена", show_alert=True)

    user = await get_or_create_user(
        user_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
        last_name=callback_query.from_user.last_name,
        session=session
    )
    room_member = await session.get(RoomMember, (user.id, room.id))

    if not room_member or room_member.role != UserRole.ADMIN:
        return await callback_query.answer("🚫 У вас нет прав администратора в этой комнате", show_alert=True)

    if action == "create_queue":
        await state.set_state(CreateQueueState.waiting_for_queue_name)
        await state.update_data(room_id=room_id)
        return await callback_query.message.answer(
            "⌨️ Введите название очереди:",
            reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
        )
    elif action == "settings":
        return await room_settings_handle(callback_query, room_id, session=session)
    elif action in ("members", "kick_member"):
        members = await get_room_member(room_id, except_user_id=user.id, session=session)
        items_per_page = MEMBERS_PER_PAGE
        members_to_show = members[0:items_per_page]

        return await callback_query.message.edit_text(
            f'👥 Участники комнаты <b>{safe_html(room.name)}</b>:',
            reply_markup=InlineKeyboardBuilderFactory.room_members_keyboard(members_to_show, room_id, 0, len(members), items_per_page),
            parse_mode="HTML"
        )


async def room_settings_handle(callback_query: CallbackQuery, room_id: int, session: AsyncSession):
    room = await get_room_by_id(room_id, session=session)
    if not room:
        return await callback_query.answer("⚠️ Комната не найдена", show_alert=True)

    return await callback_query.message.edit_text(
        f"⚙️ Настройки комнаты <b>{safe_html(room.name)}</b>",
        reply_markup=InlineKeyboardBuilderFactory.room_settings_keyboard(room_id),
        parse_mode="HTML" 
    )


@router.callback_query(F.data.startswith("room_admin_settings:"))
async def room_settings_callback_handler(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback_query.data.split(":")
    action = parts[1]
    room_id = int(parts[2])

    if action == "rename":
        await rename_room(callback_query, room_id, state)
    elif action == "delete":
        # Prompt confirmation
        room = await get_room_by_id(room_id, session=session)
        if not room:
            return await callback_query.answer("⚠️ Комната не найдена", show_alert=True)
        return await callback_query.message.edit_text(
            f"⚠️ <b>Вы уверены, что хотите удалить комнату «{safe_html(room.name)}»?</b>\n\n"
            "Все очереди и записи участников в ней будут удалены безвозвратно!",
            reply_markup=InlineKeyboardBuilderFactory.room_delete_confirmation_keyboard(room_id),
            parse_mode="HTML"
        )
    elif action == "confirm_delete":
        await delete_room_handler(callback_query, room_id, session=session)


async def delete_room_handler(callback_query: CallbackQuery, room_id: int, session: AsyncSession):
    user_id = callback_query.from_user.id
    room = await get_room_by_id(room_id, session=session)
    if not room:
        return await callback_query.answer("⚠️ Комната не найдена", show_alert=True)

    # Check that user is admin or creator
    room_member = await session.get(RoomMember, (user_id, room_id))
    if not room_member or room_member.role != UserRole.ADMIN:
        return await callback_query.answer("🚫 У вас нет прав для удаления этой комнаты", show_alert=True)

    success = await delete_room(room_id, session=session)
    await session.commit()

    if not success:
        return await callback_query.answer("❌ Ошибка при удалении комнаты", show_alert=True)

    await callback_query.answer("🗑 Комната удалена")
    try:
        await callback_query.message.delete()
    except Exception:
        pass


async def rename_room(callback_query: CallbackQuery, room_id: int, state: FSMContext):
    await state.set_state(CreateRoomState.waiting_for_room_rename)
    await state.update_data(room_id=room_id)
    return await callback_query.message.answer(
        "✏️ Введите новое название комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


@router.message(CreateRoomState.waiting_for_room_rename)
async def rename_room_text_handler(message: Message, state: FSMContext, session: AsyncSession):
    room_name = message.text
    data = await state.get_data()
    room_id = data.get("room_id")

    if not room_name or room_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Переименование комнаты отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    room_name = room_name.strip()
    room = await session.get(RoomModel, room_id)
    if not room:
        await state.clear()
        return await message.answer(
            "⚠️ Комната не существует",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    # Check unique constraint (name, creator_id)
    existing = await get_room_by_name_and_creator(room_name, room.creator_id, session=session)
    if existing and existing.id != room.id:
        return await message.answer(
            f"⚠️ У вас уже есть комната с названием «{safe_html(room_name)}». Введите другое имя:",
            reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
        )

    room.name = room_name
    await session.commit()
    await state.clear()

    return await message.answer(
        f"✅ Комната успешно переименована в <b>{safe_html(room.name)}</b>!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == MainMenuButtons.JOIN_ROOM.value)
async def join_room_command(message: Message, state: FSMContext):
    await state.set_state(JoinRoomState.waiting_for_invite_code)
    return await message.answer(
        "🔑 Введите код приглашения комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


@router.message(JoinRoomState.waiting_for_invite_code)
async def process_invite_code(message: Message, state: FSMContext, session: AsyncSession):
    invite_code = message.text
    if not invite_code or invite_code == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Присоединение к комнате отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    invite_code = invite_code.strip().upper()
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        session=session
    )

    status, room = await join_room_with_unicode(user.id, invite_code, session=session)
    await session.commit()
    await state.clear()

    if status == "not_found":
        return await message.answer(
            f"🔍 Комната с кодом «{safe_html(invite_code)}» не найдена",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
    elif status == "already_in":
        return await message.answer(
            f"ℹ️ Вы уже состоите в комнате <b>{safe_html(room.name)}</b>",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
            parse_mode="HTML"
        )

    return await message.answer(
        f"✅ Вы успешно присоединились к комнате <b>{safe_html(room.name)}</b>!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("queue:"))
async def queue_callback(callback_query: CallbackQuery, session: AsyncSession, room_id: int | None = None):
    if not room_id:
        room_id = int(callback_query.data.split(":")[1])
    
    user_id = callback_query.from_user.id
    stmt = (
        select(RoomModel)
        .options(selectinload(RoomModel.members), selectinload(RoomModel.queues))
        .filter(RoomModel.id == room_id)
    )
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()

    if not room:
        return await callback_query.answer("⚠️ Такая комната не существует", show_alert=True)

    room_users_ids = [m.user_id for m in room.members]
    if user_id not in room_users_ids:
        return await callback_query.answer("🚫 Вы не участник комнаты", show_alert=True)

    queues = room.queues
    buttons = [(queue.name, f"open_queue:{queue.id}") for queue in queues]
    buttons.append(("⬅️ Назад", f"room:{room.id}"))
    
    return await callback_query.message.edit_text(
        f"📋 Очереди в комнате <b>{safe_html(room.name)}</b>:",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            buttons, 
            adjust=[2] * len(queues) if queues else [1]
        ),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("mem:list:"))
async def process_member_list(callback_query: CallbackQuery, session: AsyncSession):
    parts = callback_query.data.split(":")
    room_id = int(parts[2])
    page = int(parts[4])

    room = await get_room_by_id(room_id, session=session)
    if not room:
        return await callback_query.answer("⚠️ Комната не найдена", show_alert=True)

    members = await get_room_member(room_id, except_user_id=callback_query.from_user.id, session=session)
    items_per_page = MEMBERS_PER_PAGE
    start = page * items_per_page
    end = start + items_per_page
    members_to_show = members[start:end]

    await callback_query.answer()
    return await callback_query.message.edit_text(
        f'👥 Участники комнаты <b>{safe_html(room.name)}</b>:',
        reply_markup=InlineKeyboardBuilderFactory.room_members_keyboard(members_to_show, room_id, page, len(members), items_per_page),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("room_view:"))
async def process_room_view(callback_query: CallbackQuery, bot: Bot, session: AsyncSession):
    return await room_callback(callback_query, bot=bot, session=session)


async def join_room_by_link(message: Message, user_id: int, invite_code: str, session: AsyncSession):
    status, room = await join_room_with_unicode(user_id, invite_code, session=session)
    await session.commit()

    if status == "not_found":
        return await message.answer(
            "🔍 Комната не найдена",
            reply_markup=ReplyKeyboardBuilderFactory.create_main_menu_keyboard()
        )
    elif status == "already_in":
        return await message.answer(
            f"ℹ️ Вы уже состоите в комнате <b>{safe_html(room.name)}</b>",
            reply_markup=ReplyKeyboardBuilderFactory.create_main_menu_keyboard(),
            parse_mode="HTML"
        )

    return await message.answer(
        f"✅ Вы успешно присоединились к комнате <b>{safe_html(room.name)}</b>!",
        reply_markup=ReplyKeyboardBuilderFactory.create_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.message(Command("bind"), F.chat.type.in_({"group", "supergroup"}), ChatAdminFilter())
async def cmd_bind(message: Message, command: CommandObject, session: AsyncSession):
    if not command.args:
        return await message.answer("⌨️ Использование: <code>/bind [код комнаты]</code>", parse_mode="HTML")

    invite_code = command.args.strip().upper()
    user_id = message.from_user.id
    chat_id = message.chat.id

    room = await get_room_by_invite_code(invite_code, session=session)
    if not room or room.creator_id != user_id:
        return await message.answer("🚫 Только создатель комнаты может привязать её к группе")

    room = await bind_room_to_chat(room.id, chat_id, session=session)
    await session.commit()
    await message.answer(f"🔗 Группа успешно привязана к комнате <b>{safe_html(room.name)}</b>", parse_mode="HTML")


@router.message(Command("unbind"), F.chat.type.in_({"group", "supergroup"}), ChatAdminFilter())
async def cmd_unbind(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    chat_id = message.chat.id

    stmt = select(RoomModel).filter_by(telegram_chat_id=chat_id)
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()

    if not room or room.creator_id != user_id:
        return await message.answer("🚫 Только создатель привязанной комнаты может отвязать группу")

    room.telegram_chat_id = None
    await session.commit()
    await message.answer(f"🔗 Группа успешно отвязана от комнаты <b>{safe_html(room.name)}</b>", parse_mode="HTML")
