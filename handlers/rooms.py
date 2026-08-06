from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.users import get_or_create_user
from accessors.rooms import (
    get_room_by_name_and_creator,
    get_rooms_for_user,
    get_room_by_id,
    get_members_of_room,
)
from accessors.queues import get_queues_by_room, reindex_queue
from database.session import async_session
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from database.models import QueueEntry, RoomMember, RoomModel, UserModel, UserRole, QueueModel
from enums import MainMenuButtons
from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from main import CreateQueueState, CreateRoomState, JoinRoomState


async def create_room_command(message, state: FSMContext):
    await state.set_state(CreateRoomState.waiting_for_room_name)
    await message.answer(
        "Введите название комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


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

    # create_room_with_unique_code uses async_session directly
    from accessors.rooms import create_room_with_unique_code  # import here to avoid cycles
    room = await create_room_with_unique_code(room_name, user.id)

    await state.clear()
    await message.answer(
        f"Комната '{room.name}' успешно создана!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )


async def user_rooms_command(message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    rooms = await get_rooms_for_user(user.id)

    if not rooms:
        await message.answer(
            "У вас нет комнат\nСоздайте комнату или присоединитесь к существующей",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    await message.answer(
        f"Ваши комнаты:\n",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            [(room.name, f"room:{room.id}") for room in rooms], 
            adjust=[2] * len(rooms)),
            parse_mode="HTML"
    )


async def room_callback(callback_query: CallbackQuery):
    room_id = int(callback_query.data.split(":")[1])
    room = await get_room_by_id(room_id)
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    async with async_session() as session:
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


async def back_rooms(callback_query: CallbackQuery):
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    rooms = await get_rooms_for_user(user.id)

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


async def room_settings_callback(callback_query: CallbackQuery, state: FSMContext):
    parts = callback_query.data.split(":")
    action = parts[1]
    room_id = int(parts[2])

    room = await get_room_by_id(room_id)
    user = await get_or_create_user(callback_query.from_user.id, callback_query.from_user.username)
    async with async_session() as session:
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
        members = await get_members_of_room(room.id)
        member_list = "\n".join([f"- @{member.username or 'User'} (ID: {member.id})" for member in members])
        await callback_query.message.answer(
            f"Участники комнаты '{room.name}':\n{member_list}"
        )


async def join_room_command(message, state: FSMContext):
    await state.set_state(JoinRoomState.waiting_for_invite_code)
    await message.answer(
        "Введите код приглашения комнаты:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )


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


# End of rooms handlers
