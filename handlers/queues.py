from accessors.rooms import get_room_with_queue
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.queues import delete_queue, get_entry_by_position, get_entry_by_user_id, get_queue_with_data, reindex_queue, swap_users_in_queue
from aiogram.fsm.context import FSMContext
from accessors.users import get_or_create_user
from database.session import async_session
from database.models import QueueModel, QueueEntry, UserModel, RoomMember, UserRole
from sqlalchemy import delete, select
from enums import QueueInlineButtons, MainMenuButtons, WritingCommentButtons
from aiogram.types import CallbackQuery, Message
from aiogram import F, Bot, Router
import logging

from states import CreateQueueState, SwapEntriesState

logger = logging.getLogger(__name__)

router = Router()

async def generate_queue_message(user_id: int, queue: QueueModel):
    text_lines = [f"Очередь <b>{queue.name}</b>", ""]
    user_ids_in_queue = [entry.user_id for entry in queue.entries]

    room = await get_room_with_queue(queue.id)
    logger.info(room.id)
    room_members_admin_ids = [member.user_id for member in room.members if member.role == UserRole.ADMIN]
    logger.info(room_members_admin_ids)

    if not queue.entries:
        text_lines.append("<i>Пусто...</i>")
    else:
        sorted_entries = sorted(queue.entries, key=lambda x: x.position)
        for entry in sorted_entries:
            user_label = entry.user.username or entry.user.id
            text_lines.append(f"{entry.position}. @{user_label}")

    is_in_queue = user_id in user_ids_in_queue

    keyboard = InlineKeyboardBuilderFactory.queue_inline_keyboard(user_id, is_in_queue, room_members_admin_ids, queue.id, queue.room_id)
    
    return "\n".join(text_lines), keyboard

@router.message(CreateQueueState.waiting_for_queue_name)
async def process_queue_name(message, state):
    queue_name = message.text
    room_id = (await state.get_data()).get("room_id")
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
    return await message.answer(
        f"Очередь '{queue.name}' успешно создана в комнате с ID {room_id}!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )

@router.callback_query(F.data.startswith("queue_back:"))
@router.callback_query(F.data.startswith("open_queue:"))
async def open_queue_callback(callback_query: CallbackQuery):
    queue_id = int(callback_query.data.split(":")[1])
    async with async_session() as session:
        queue = await get_queue_with_data(session, queue_id)

    if not queue:
        return await callback_query.answer("Очередь не найдена")

    text, keyboard = await generate_queue_message(callback_query.from_user.id, queue)
    await callback_query.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    return await callback_query.answer()

@router.callback_query(F.data.startswith("queue_control:"))
async def queue_control_handler(callback_query: CallbackQuery, state: FSMContext):
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

        elif command == "skip":
            if not is_already_in:
                return await callback_query.answer("Вас нет в этой очереди")

            current_user_entry = next((e for e in queue.entries if e.user_id == user_id), None)
            
            if not current_user_entry:
                return await callback_query.answer("Ошибка данных")

            target_entry = next((e for e in queue.entries if e.position == current_user_entry.position + 1), None)

            if not target_entry:
                return await callback_query.answer("Вы уже последний в очереди, некого пропускать", show_alert=True)

            success = await swap_users_in_queue(session, queue_id, user_id, target_entry.user_id)
            
            if success:
                await session.commit()
                await callback_query.answer(f"Вы пропустили @{target_entry.user.username or 'пользователя'} вперед")
            else:
                await callback_query.answer("Не удалось выполнить пропуск")

        elif command == "swap":
            await swap_entries_handler(callback_query, state, user_id, queue_id)

        await session.commit()
        
        updated_queue = await get_queue_with_data(session, queue_id)

    text, keyboard = await generate_queue_message(user_id, updated_queue)
    try:
        return await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        logger.info("Ошибка при изменении сообщения очереди")
        pass

@router.callback_query(F.data.startswith("queue_admin:"))
async def queue_admin_handler(callback_query: CallbackQuery, state: FSMContext):
    method = callback_query.data.split(":")[1]
    queue_id = callback_query.data.split(":")[2]

    if method == "settings":
        await queue_settings_handler(callback_query)
    elif method == "delete":
        await queue_delete_handler(callback_query)
    elif method == "rename":
        await queue_rename_handler(callback_query, state)

    return

async def queue_settings_handler(callback_query: CallbackQuery):
    queue_id = callback_query.data.split(":")[2]
    keyboard = InlineKeyboardBuilderFactory.queue_settings_keyboard(queue_id)

    return await callback_query.message.edit_text(
        "Выберите действие",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def queue_delete_handler(callback_query: CallbackQuery, state: FSMContext):
    from handlers.rooms import queue_callback

    queue_id = int(callback_query.data.split(":")[2])
    room_id = await delete_queue(queue_id)

    if not room_id:
        return await callback_query.message.delete()

    await callback_query.answer("Очередь удалена")

    return await queue_callback(callback_query, room_id)

@router.message(CreateQueueState.waiting_for_queue_rename)
async def queue_rename_text_handler(message, state: FSMContext):
    queue_name = message.text
    queue_id = (await state.get_data()).get("queue_id")
    if queue_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "Переименование очереди отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
        return

    async with async_session() as session:
        result = await session.execute(
            select(QueueModel)
            .filter(QueueModel.id == queue_id)
        )
        queue = result.scalar_one_or_none()

        if not queue:
            await message.answer(
                "Очередь не существует",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        queue.name = queue_name
        await session.commit()

    await state.clear()
    return await message.answer(
        f"Очередь <b>{queue.name}</b> успешно переименована!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )

async def queue_rename_handler(callback_query: CallbackQuery, state: FSMContext):
    queue_id = int(callback_query.data.split(":")[2])
    await state.set_state(CreateQueueState.waiting_for_queue_rename)
    await state.update_data(queue_id=queue_id)
    return await callback_query.message.answer(
        "Введите новое название очереди:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )

async def swap_entries_handler(callback_query: CallbackQuery, state: FSMContext, user_id: int, queue_id: int):
    await state.set_state(SwapEntriesState.waiting_postition)
    await state.update_data(queue_id=queue_id)
    return await callback_query.message.answer(
        "Введите номер позиции, на которую Вы хотите встать",
        reply_markup=ReplyKeyboardBuilderFactory().create_cancel_swap_keyboard()
    )

@router.message(SwapEntriesState.waiting_postition)
async def process_position_message(message: Message, state: FSMContext):
    data = await state.get_data()
    position = message.text
    queue_id = data["queue_id"]
    user_id = message.from_user.id
    try:
        position = int(position)
    except Exception as e:
        logger.warning(f"Position message error: {e}")
        return await message.answer("Неправильный формат позиции. Повторите попытку")

    entry = await get_entry_by_position(queue_id, position, user_id)

    if entry is None:
        return message.answer("Ошибка при выборе позиции. Повторите попытку")

    await state.set_state(SwapEntriesState.waiting_comment)
    await state.update_data(target_user_id=entry.user_id, target_pos=position)
    return message.answer(
        f"Вы выбрали место <b>№{position}. @{entry.user.username}</b>. Введите комментарий (опционально)",
        reply_markup=ReplyKeyboardBuilderFactory.create_writing_comment_keyboard(),
        parse_mode="HTML"
    )

@router.message(SwapEntriesState.waiting_postition, F.data == MainMenuButtons.CANCEL.value)
async def cancel_writing_comment(message: Message, state: FSMContext):
    await state.clear()
    return await message.answer(
        "Отмена запроса на смену позиции",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

@router.message(SwapEntriesState.waiting_comment)
async def send_comment_handler(message: Message, state: FSMContext, bot: Bot):
    comment = message.text
    await state.update_data(comment=comment)
    await send_swap_offer(message, state, bot)

@router.message(SwapEntriesState.waiting_comment, F.data == WritingCommentButtons.NO_COMMENT.value)
async def send_swap_offer(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    queue_id = data["queue_id"]
    user_id_from = message.from_user.id
    user_from = message.from_user.username
    target_pos = data["target_pos"]
    target_user_id = data["target_user_id"]
    comment = data["comment"]

    entry = await get_entry_by_user_id(queue_id, user_id_from)

    text = (f"<b>Запрос поменяться местами</b>\n"
            f"Очередь <b>{entry.queue.name}</b>\n"
            f"Пользователь @{user_from} (место {entry.position}) хочет поменяться с Вами (место {target_pos})")

    if comment:
        text += f'Комментарий: <i>{comment}</i>'

    await state.clear()

    await bot.send_message(
        chat_id=target_user_id, 
        text=text,
        reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(queue_id, target_pos),
        parse_mode="HTML"
    )

    return await message.answer(
        f"Предложение встать на место {target_pos} отправлено!",
        parse_mode="HTML"
    )

async def queue_back_hanlder(callback_query: CallbackQuery):
    return await open_queue_callback(callback_query)

# End of queues handlers
