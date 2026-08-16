from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from sqlalchemy.orm import selectinload

from accessors.rooms import get_room_by_chat_id, get_room_with_queue
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.queues import clear_msg_and_chat_ids, clear_notified_next_id, clear_queue_entries, clear_speaker_id, create_swap_request, delete_first_entry, delete_queue, delete_swap_request, delete_swap_request_by_users, get_entry_by_position, get_entry_by_user_id, get_entry_with_user_by_user_id, get_queue_by_id, get_queue_with_data, get_swap_request_by_id, has_active_swap_request, reindex_queue, set_notified_next_id, set_speaker_id, swap_users_in_queue, swap_users_in_queue_by_pos, update_msg_and_chat_ids
from aiogram.fsm.context import FSMContext
from accessors.users import get_or_create_user
from database.session import async_session
from database.models import QueueModel, QueueEntry, SwapRequest, UserModel, RoomMember, UserRole
from sqlalchemy import delete, select
from enums import MainMenuButtons, WritingCommentButtons
from aiogram.types import CallbackQuery, Message
from aiogram import F, Bot, Router
import logging

from handlers.filters.admin_filter import ChatAdminFilter
from states import CreateQueueState, SwapEntriesState

logger = logging.getLogger(__name__)

router = Router()

async def update_live_queue(bot: Bot, queue_id: int):
    queue = await get_queue_by_id(queue_id)
    if not queue or not queue.last_msg_id:
        return

    text, keyboard = await generate_queue_message(user_id=0, queue=queue, in_group=True, bot=bot)

    try:
        await bot.edit_message_text(
            chat_id=queue.last_chat_id,
            message_id=queue.last_msg_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        elif "message to edit not found" in str(e):
            await clear_msg_and_chat_ids(queue_id)

async def generate_queue_message(
    user_id: int, 
    queue: QueueModel, 
    in_group: bool = False,
    bot: Bot = None
    ):
    text_lines = [f"📋 Очередь <b>{queue.name}</b>", ""]
    user_ids_in_queue = [entry.user_id for entry in queue.entries]

    room = await get_room_with_queue(queue.id)
    room_members_admin_ids = [member.user_id for member in room.members if member.role == UserRole.ADMIN]

    if not queue.entries:
        text_lines.append("🫙 <i>Пусто...</i>")
    else:
        sorted_entries = sorted(queue.entries, key=lambda x: x.position)
        for entry in sorted_entries:
            user_label = entry.user.username or entry.user.id
            text_lines.append(f"{entry.position}. @{user_label}")

    is_in_queue = user_id in user_ids_in_queue

    bot_info = await bot.get_me()

    keyboard = InlineKeyboardBuilderFactory.queue_inline_keyboard(
        user_id, 
        is_in_queue, 
        room_members_admin_ids, 
        queue.id, 
        queue.room_id,
        in_group,
        bot_info.username)
    
    return "\n".join(text_lines), keyboard

@router.message(CreateQueueState.waiting_for_queue_name)
async def process_queue_name(message, state):
    queue_name = message.text
    room_id = (await state.get_data()).get("room_id")
    if queue_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "❌ Создание очереди отменено",
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
                "⚠️ Вы не являетесь администратором комнаты",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        queue = QueueModel(name=queue_name, room_id=room_id)
        session.add(queue)
        await session.commit()

    await state.clear()
    return await message.answer(
        f"✅ Очередь '{queue.name}' успешно создана в комнате с ID {room_id}!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )

@router.callback_query(F.data.startswith("queue_back:"))
@router.callback_query(F.data.startswith("open_queue:"))
async def open_queue_callback(callback_query: CallbackQuery, bot: Bot, queue_id: int | None = None):
    data = callback_query.data.split(":")

    if queue_id is None:
        queue_id = int(data[1])
    
    async with async_session() as session:
        queue = await get_queue_with_data(session, queue_id)

    if not queue:
        return await callback_query.answer("⚠️ Очередь не найдена")

    if callback_query.data.startswith("open_queue:") and len(data) > 2:
        chat_id = data[2]
        await bot.pin_chat_message(chat_id, callback_query.message.message_id)

    text, keyboard = await generate_queue_message(
        callback_query.from_user.id, 
        queue, 
        callback_query.message.chat.type in ["group", "supergroup"],
        bot
    )
    sent_message = await callback_query.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    if callback_query.message.chat.type in ["group", "supergroup"]:
        await update_msg_and_chat_ids(queue_id, sent_message.message_id, sent_message.chat.id)

    return await callback_query.answer()

@router.callback_query(F.data.startswith("queue_control:"))
async def queue_control_handler(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    data = callback_query.data.split(":")
    command, queue_id = data[1], int(data[2])
    user_id = callback_query.from_user.id

    async with async_session() as session:
        queue = await get_queue_with_data(session, queue_id)
        if not queue:
            return await callback_query.answer("⚠️ Очередь не существует")

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
                return await callback_query.answer("ℹ️ Вы уже записаны!")
            
            new_entry = QueueEntry(
                queue_id=queue_id, 
                user_id=user_id, 
                position=len(queue.entries) + 1
            )
            session.add(new_entry)

            if len(queue.entries) == 0:
                queue.current_speaker_id = user_id

            if len(queue.entries) == 1:
                queue.notified_next_id = user_id
            
            await callback_query.answer("✅ Вы записались в очередь")

        elif command == "exit":
            if not is_already_in:
                return await callback_query.answer("⚠️ Вас нет в этой очереди")
            
            await session.execute(
                delete(QueueEntry).filter_by(queue_id=queue_id, user_id=user_id)
            )

            await session.flush()

            await reindex_queue(session, queue_id)
            
            await session.commit()
            
            await callback_query.answer("🏃 Вы вышли из очереди")

        elif command == "skip":
            if not is_already_in:
                return await callback_query.answer("⚠️ Вас нет в этой очереди")

            current_user_entry = next((e for e in queue.entries if e.user_id == user_id), None)
            
            if not current_user_entry:
                return await callback_query.answer("❓ Ошибка данных")

            target_entry = next((e for e in queue.entries if e.position == current_user_entry.position + 1), None)

            if not target_entry:
                return await callback_query.answer("ℹ️ Вы уже последний в очереди, некого пропускать", show_alert=True)

            success = await swap_users_in_queue(session, queue_id, user_id, target_entry.user_id)
            
            if success:
                await session.commit()
                await callback_query.answer(f"⏭ Вы пропустили @{target_entry.user.username or 'пользователя'} вперед")
            else:
                await callback_query.answer("❌ Не удалось выполнить пропуск")

        elif command == "swap":
            return await swap_entries_handler(callback_query, state, user_id, queue_id)

        await session.commit()

        updated_queue = await get_queue_with_data(session, queue_id)
        await process_queue_updates(bot, queue_id)

    text, keyboard = await generate_queue_message(
        user_id, 
        updated_queue, 
        callback_query.message.chat.type in ["group", "supergroup"],
        bot
    )
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
async def queue_admin_handler(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    method = callback_query.data.split(":")[1]
    queue_id = int(callback_query.data.split(":")[2])

    if method == "settings":
        await queue_settings_handler(callback_query)
    elif method == "delete":
        await queue_delete_handler(callback_query) 
    elif method == "rename":
        await queue_rename_handler(callback_query, state)
    elif method == "clear":
        await queue_clear_handler(callback_query)
    elif method == "move":
        await queue_move_handler(callback_query, bot)
    
    await process_queue_updates(bot, queue_id)
    return

async def queue_move_handler(callback_query: CallbackQuery, bot: Bot):
    queue_id = int(callback_query.data.split(":")[2])
    user_id = callback_query.from_user.id

    room = await get_room_with_queue(queue_id)
    room_admins_ids = [member.user_id for member in room.members if member.role == UserRole.ADMIN]

    first_entry = await get_entry_by_position(queue_id, 1)

    allowed_users = room_admins_ids + [first_entry.user_id]

    if user_id not in allowed_users:
        return await callback_query.answer(
            "🛑 У вас недостаточно прав или сейчас не ваша очередь",
            show_alert=True
        )

    await delete_first_entry(queue_id)
    await callback_query.answer("↕️ Вы сместили очередь")

    async with async_session() as session:
        await reindex_queue(session, queue_id)

        await session.commit()

    queue = await get_queue_by_id(queue_id)

    text, keyboard = await generate_queue_message(
            user_id, 
            queue, 
            callback_query.message.chat.type in ["group", "supergroup"],
            bot
    )
    try:
        return await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        logger.info("Ошибка при изменении сообщения очереди")
        pass

@router.callback_query(F.data.startswith("confirm:"))
async def confrim_action_handler(callback_query: CallbackQuery, bot: Bot):
    action = callback_query.data.split(":")[2]
    target_id = int(callback_query.data.split(":")[1])

    if action == "clear":
        await clear_queue_action(callback_query, target_id)
        await open_queue_callback(callback_query, bot, target_id)
        await process_queue_updates(bot, target_id)

@router.callback_query(F.data.startswith("back"))
async def cancel_action_handler(callback_query: CallbackQuery, bot: Bot):
    action = callback_query.data.split(":")[2]
    target_id = int(callback_query.data.split(":")[1])

    if action == "clear":
        await open_queue_callback(callback_query, bot, target_id)

async def clear_queue_action(callback_query: CallbackQuery, queue_id: int):
    await clear_queue_entries(queue_id)

    await callback_query.answer("🧹 Очередь очищена")

async def queue_clear_handler(callback_query: CallbackQuery):
    method = callback_query.data.split(":")[1]
    queue_id = int(callback_query.data.split(":")[2])
    queue = await get_queue_by_id(queue_id)

    await callback_query.message.edit_text(
        text=f"❓ Очистить очередь {queue.name}?",
        reply_markup=InlineKeyboardBuilderFactory().create_confirmation_keyboard(method, queue_id)
    )

async def queue_settings_handler(callback_query: CallbackQuery):
    queue_id = callback_query.data.split(":")[2]
    keyboard = InlineKeyboardBuilderFactory.queue_settings_keyboard(queue_id)

    return await callback_query.message.edit_text(
        "🛠 Выберите действие",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def queue_delete_handler(callback_query: CallbackQuery, state: FSMContext):
    from handlers.rooms import queue_callback

    queue_id = int(callback_query.data.split(":")[2])
    room_id = await delete_queue(queue_id)

    if not room_id:
        return await callback_query.message.delete()

    await callback_query.answer("🗑 Очередь удалена")

    return await queue_callback(callback_query, room_id)

@router.message(CreateQueueState.waiting_for_queue_rename)
async def queue_rename_text_handler(message, state: FSMContext):
    queue_name = message.text
    queue_id = (await state.get_data()).get("queue_id")
    if queue_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        await message.answer(
            "❌ Переименование очереди отменено",
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
                "⚠️ Очередь не существует",
                reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
            await state.clear()
            return

        queue.name = queue_name
        await session.commit()

    await state.clear()
    return await message.answer(
        f"✅ Очередь <b>{queue.name}</b> успешно переименована!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )

async def queue_rename_handler(callback_query: CallbackQuery, state: FSMContext):
    queue_id = int(callback_query.data.split(":")[2])
    await state.set_state(CreateQueueState.waiting_for_queue_rename)
    await state.update_data(queue_id=queue_id)
    return await callback_query.message.answer(
        "✏️ Введите новое название очереди:",
        reply_markup=ReplyKeyboardBuilderFactory().build_keyboard([MainMenuButtons.CANCEL.value])
    )

async def swap_entries_handler(callback_query: CallbackQuery, state: FSMContext, user_id: int, queue_id: int):
    active_swap_request = await has_active_swap_request(queue_id, user_id)
    if active_swap_request:
        if active_swap_request.target_id == user_id:
            text = (f"🤝 У Вас уже есть открытый запрос на смену позиции в очереди <b>{active_swap_request.queue.name}</b>\n"
                f"Ответьте на запрос от пользователя @{active_swap_request.sender.username} или дождитесь отмены")

            entry_from = await get_entry_with_user_by_user_id(queue_id, active_swap_request.sender_id)

            entry_to = await get_entry_with_user_by_user_id(queue_id, active_swap_request.target_id)

            text += (f"\n\n✨ <b>Запрос поменяться местами</b>\n"
                        f"Очередь <b>{entry_from.queue.name}</b>\n"
                        f"Пользователь @{entry_from.user.username} (место {entry_from.position}) хочет поменяться с Вами (место {entry_to.position})")

            return await callback_query.message.answer(
                text=text,
                reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(active_swap_request.id),
                parse_mode="HTML"
            )

        elif active_swap_request.sender_id == user_id:
            text = (f"⏳ У Вас уже есть открытый запрос на смену позиции в очереди <b>{active_swap_request.queue.name}</b>\n"
                f"Дождитесь ответа от @{active_swap_request.target.username} или отмените его")
            return await callback_query.message.answer(
                text=text,
                reply_markup=InlineKeyboardBuilderFactory().create_cancel_swap_request_keyboard(active_swap_request.id),
                parse_mode="HTML"
            )
        
    
    await state.set_state(SwapEntriesState.waiting_postition)
    await state.update_data(queue_id=queue_id)
    return await callback_query.message.answer(
        "🔢 Введите номер позиции, на которую Вы хотите встать",
        reply_markup=ReplyKeyboardBuilderFactory().create_cancel_swap_keyboard()
    )

@router.callback_query(F.data.startswith("cancel_request:"))
async def cancel_swap_request_inline_handler(callback_query: CallbackQuery):
    swap_request_id = int(callback_query.data.split(":")[1])
    request = await get_swap_request_by_id(swap_request_id)

    if request is None:
        await callback_query.answer("Запрос не найден")
        await callback_query.message.delete()
        return 

    success = await delete_swap_request(swap_request_id)

    logger.info(success)

    if not success:
        await callback_query.answer("Ошибка при отмене запроса")
    else:
        await callback_query.answer("❌ Запрос отменён")

    return await callback_query.message.delete()

@router.message(SwapEntriesState.waiting_postition)
async def process_position_message(message: Message, state: FSMContext):
    data = await state.get_data()
    position = message.text
    queue_id = data["queue_id"]
    user_id = message.from_user.id
    if position == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Смена позиции отменена",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
            )
    try:
        position = int(position)
    except Exception as e:
        logger.warning(f"Position message error: {e}")
        return await message.answer("⚠️ Неправильный формат позиции. Повторите попытку")

    entry = await get_entry_by_position(queue_id, position, user_id)

    if entry is None:
        return message.answer("⚠️ Ошибка при выборе позиции. Повторите попытку")

    await state.set_state(SwapEntriesState.waiting_comment)
    await state.update_data(target_user_id=entry.user_id, target_pos=position)
    return message.answer(
        f"🎯 Вы выбрали место <b>№{position}. @{entry.user.username}</b>\n💬 Введите комментарий (опционально)",
        reply_markup=ReplyKeyboardBuilderFactory.create_writing_comment_keyboard(),
        parse_mode="HTML"
    )

@router.message(SwapEntriesState.waiting_postition, F.data == MainMenuButtons.CANCEL.value)
async def cancel_writing_comment(message: Message, state: FSMContext):
    await state.clear()
    return await message.answer(
        "❌ Отмена запроса на смену позиции",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

@router.message(F.text == WritingCommentButtons.NO_COMMENT.value, SwapEntriesState.waiting_comment)
async def send_swap_offer(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    queue_id = data["queue_id"]
    user_id_from = message.from_user.id
    user_from = message.from_user.username
    target_pos = data["target_pos"]
    target_user_id = data["target_user_id"]
    comment = data.get("comment") or None

    entry = await get_entry_by_user_id(queue_id, user_id_from)

    text = (f"🤝 <b>Запрос поменяться местами</b>\n"
            f"Очередь <b>{entry.queue.name}</b>\n"
            f"Пользователь @{user_from} (место {entry.position}) хочет поменяться с Вами (место {target_pos})")

    if comment:
        text += f'\n\n💬 Комментарий: <i>{comment}</i>'

    swap_request = await create_swap_request(queue_id, user_id_from, target_user_id)
    if swap_request is None:
        await state.clear()
        return await message.answer("❌ Ошибка создания запроса", reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard())

    await state.clear()

    await bot.send_message(
        chat_id=target_user_id, 
        text=text,
        reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(swap_request.id),
        parse_mode="HTML"
    )

    return await message.answer(
        f"📨 Предложение встать на место {target_pos} отправлено!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )

@router.message(SwapEntriesState.waiting_comment)
async def send_comment_handler(message: Message, state: FSMContext, bot: Bot):
    comment = message.text
    await state.update_data(comment=comment)
    await send_swap_offer(message, state, bot)

@router.callback_query(F.data.startswith("swap:"))
async def swap_offer_handle(callback_query: CallbackQuery, bot: Bot):
    _, action, request_id = callback_query.data.split(":")
    request_id = int(request_id)

    async with async_session() as session:
        result = await session.execute(
            select(SwapRequest).options(
                selectinload(SwapRequest.queue).selectinload(QueueModel.entries).selectinload(QueueEntry.user),
                selectinload(SwapRequest.sender), 
                selectinload(SwapRequest.target))
                .filter_by(id=request_id)
        )
        request = result.scalar_one_or_none()

        if not request:
            await callback_query.answer("⚠️ Запрос уже недействителен")
            return await callback_query.message.delete()

        if action == "accept":
            success, res_1, res_2 = await swap_users_in_queue(
                session, request.queue_id, request.sender_id, request.target_id
            )
            
            if success:
                entry_sender = res_1 if res_1.user_id == request.sender_id else res_2
                entry_target = res_2 if res_1.user_id == request.sender_id else res_1
                await bot.send_message(
                    request.sender_id, 
                    f"✅ Обмен принят!\nОчередь <b>{request.queue.name}</b>\nВаше место {entry_sender.position}"
                )
                await callback_query.message.edit_text(
                    f"✅ Обмен выполнен! Очередь <b>{request.queue.name}</b>\nВаше место {entry_target.position}"
                )
                await session.delete(request)
                await session.commit()
            else:
                await callback_query.answer("❌ Ошибка: кто-то вышел из очереди")

        elif action == "decline":
            await bot.send_message(request.sender_id, f"❌ Пользователь @{request.target.username} отказал в обмене")
            await callback_query.message.edit_text(f"❌ Вы отклонили запрос @{request.sender.username}")
            await session.delete(request)
            await session.commit()

    user_id = callback_query.from_user.id
    queue = request.queue

    text, keyboard = await generate_queue_message(
        user_id, 
        queue, 
        callback_query.message.chat.type in ["group", "supergroup"],
        bot
    )
    try:
        return await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        logger.info("⚠️ Ошибка при изменении сообщения очереди")
        pass

    await process_queue_updates(bot, request.queue_id)

async def queue_back_hanlder(callback_query: CallbackQuery):
    return await open_queue_callback(callback_query)

@router.message(Command("queue"), ChatAdminFilter())
async def send_queue_message_command(message: Message):
    user_id = message.from_user.id
    room = await get_room_by_chat_id(message.chat.id)

    queues = room.queues

    room_users_ids = [member.user_id for member in room.members]

    if not room:
        return await message.answer(
            "⚠️ Такая комната не существует",
            show_alert=True
        )

    if user_id and user_id not in room_users_ids:
        return await message.answer(
            "🚫 Вы не участник комнаты",
            show_alert=True
        )

    buttons = [(queue.name, f"open_queue:{queue.id}:{message.chat.id}") for queue in queues]
    
    return await message.answer(
        f"📋 Очереди в комнате <b>{room.name}</b>",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            buttons, 
            adjust=[2] * len(queues),),
        parse_mode="HTML"
    )

async def process_queue_updates(bot: Bot, queue_id: int):
    await update_live_queue(bot, queue_id)

    queue = await get_queue_by_id(queue_id)
    if not queue:
        return

    if not queue.entries:
        await clear_notified_next_id(queue_id)
        return await clear_speaker_id(queue_id)

    if len(queue.entries) > 0:
        first_student = await get_entry_by_position(queue_id, 1)
        if queue.current_speaker_id and queue.current_speaker_id == first_student.user_id:
            pass
        else:
            await set_speaker_id(queue_id, first_student.user_id)
            try:
                await bot.send_message(
                    chat_id=first_student.user_id,
                    text=f"🔔 <b>Твоё время пришло!</b>\nПора отвечать в очереди <b>{queue.name}</b>. Удачи!",
                    parse_mode="HTML"
                )
            except Exception:
                if queue.last_chat_id:
                    await bot.send_message(
                        chat_id=queue.last_chat_id,
                        text=f"📢 @{first_student.user.username}, твоя очередь в списке «{queue.name}»!"
                    )
                logger.info("Не удалось отправить сообщение лично")

        second_student = await get_entry_by_position(queue_id, 2)
        if second_student is None:
            return await clear_notified_next_id(queue_id)
        
        if queue.notified_next_id and queue.notified_next_id == second_student.user_id:
            pass
        else:
            await set_notified_next_id(queue_id, second_student.user_id)
            try:
                await bot.send_message(
                    chat_id=second_student.user_id,
                    text=f"🔜 <b>Приготовься, ты следующий!</b>\n\nГотовься отвечать в очереди <b>{queue.name}</b>",
                    parse_mode="HTML"
                )
            except Exception:
                if queue.last_chat_id:
                    await bot.send_message(
                        chat_id=queue.last_chat_id,
                        text=f"🏃 @{second_student.user.username}, готовься, ты следующий в очереди «{queue.name}»!"
                    )
                logger.info("Не удалось отправить сообщение лично")

# End of queues handlers
