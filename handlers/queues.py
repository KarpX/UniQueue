from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.queues import get_queue_with_data, reindex_queue
from accessors.users import get_or_create_user
from database.session import async_session
from database.models import QueueModel, QueueEntry, UserModel, RoomMember, UserRole
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from enums import QueueInlineButtons, MainMenuButtons
from aiogram.types import CallbackQuery
from aiogram import F
import logging

logger = logging.getLogger(__name__)


async def generate_queue_message(user_id: int, queue: QueueModel):
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


async def process_queue_name(message, state):
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


# End of queues handlers
