import logging
from aiogram import F, Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from accessors.rooms import get_room_by_chat_id, get_room_by_id, get_room_with_queue
from accessors.queues import (
    atomic_clear_queue,
    atomic_exit_queue,
    atomic_join_queue,
    atomic_move_queue,
    atomic_skip_user,
    clear_msg_and_chat_ids,
    confirm_and_execute_swap,
    create_swap_request,
    delete_queue,
    delete_swap_request,
    get_entry_by_position,
    get_entry_by_user_id,
    get_entry_with_user_by_user_id,
    get_queue_by_id,
    get_queue_with_data,
    get_swap_request_by_id,
    has_active_swap_request,
    update_msg_and_chat_ids,
)
from accessors.users import get_or_create_user
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from database.models import QueueModel, QueueEntry, SwapRequest, UserModel, UserRole
from enums import MainMenuButtons, WritingCommentButtons
from handlers.filters.admin_filter import ChatAdminFilter
from services.queue_updater import generate_queue_message_data, schedule_live_queue_update
from states import CreateQueueState, SwapEntriesState
from utils import get_user_display_name, safe_html

logger = logging.getLogger(__name__)

router = Router()


async def generate_queue_message(
    user_id: int, 
    queue: QueueModel, 
    in_group: bool = False,
    bot: Bot = None
) -> tuple[str, any]:
    bot_username = None
    if bot:
        bot_info = await bot.get_me()
        bot_username = bot_info.username

    return await generate_queue_message_data(
        queue=queue,
        user_id=user_id,
        in_group=in_group,
        bot_username=bot_username
    )


@router.message(CreateQueueState.waiting_for_queue_name)
async def process_queue_name(message: Message, state: FSMContext, session: AsyncSession):
    queue_name = message.text
    data = await state.get_data()
    room_id = data.get("room_id")

    if not queue_name or queue_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Создание очереди отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        session=session
    )

    room = await get_room_by_id(room_id, session=session)
    if not room:
        await state.clear()
        return await message.answer(
            "⚠️ Комната не существует",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    # Check admin rights in room
    room_admins = [m.user_id for m in room.members if m.role == UserRole.ADMIN]
    if user.id not in room_admins:
        await state.clear()
        return await message.answer(
            "⚠️ Вы не являетесь администратором комнаты",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    queue = QueueModel(name=queue_name.strip(), room_id=room_id)
    session.add(queue)
    await session.commit()
    await state.clear()

    return await message.answer(
        f"✅ Очередь <b>{safe_html(queue.name)}</b> успешно создана в комнате <b>{safe_html(room.name)}</b>!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("queue_back:"))
@router.callback_query(F.data.startswith("open_queue:"))
async def open_queue_callback(callback_query: CallbackQuery, bot: Bot, session: AsyncSession, queue_id: int | None = None):
    data = callback_query.data.split(":")

    if queue_id is None:
        queue_id = int(data[1])
    
    queue = await get_queue_with_data(session, queue_id)
    if not queue:
        return await callback_query.answer("⚠️ Очередь не найдена", show_alert=True)

    if callback_query.data.startswith("open_queue:") and len(data) > 2:
        chat_id = data[2]
        try:
            await bot.pin_chat_message(chat_id, callback_query.message.message_id)
        except Exception as e:
            logger.debug(f"Failed to pin message in chat {chat_id}: {e}")

    in_group = callback_query.message.chat.type in ["group", "supergroup"]
    text, keyboard = await generate_queue_message(
        user_id=callback_query.from_user.id, 
        queue=queue, 
        in_group=in_group,
        bot=bot
    )

    try:
        sent_message = await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        if in_group:
            await update_msg_and_chat_ids(queue_id, sent_message.message_id, sent_message.chat.id, session=session)
            await session.commit()
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Error editing message in open_queue: {e}")

    return await callback_query.answer()


@router.callback_query(F.data.startswith("queue_control:"))
async def queue_control_handler(callback_query: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession):
    data = callback_query.data.split(":")
    command = data[1]
    queue_id = int(data[2])
    user_id = callback_query.from_user.id
    from_user = callback_query.from_user
    in_group = callback_query.message.chat.type in ["group", "supergroup"]

    if command == "join":
        success, message_text, queue = await atomic_join_queue(
            session=session,
            queue_id=queue_id,
            user_id=user_id,
            username=from_user.username,
            first_name=from_user.first_name,
            last_name=from_user.last_name
        )
        await session.commit()
        await callback_query.answer(message_text, show_alert=not success)
        if success:
            await process_queue_updates(bot, queue_id, session)

    elif command == "exit":
        success, message_text, queue = await atomic_exit_queue(
            session=session,
            queue_id=queue_id,
            user_id=user_id
        )
        await session.commit()
        await callback_query.answer(message_text, show_alert=not success)
        if success:
            await process_queue_updates(bot, queue_id, session)

    elif command == "skip":
        success, message_text, target_user = await atomic_skip_user(
            session=session,
            queue_id=queue_id,
            user_id=user_id
        )
        await session.commit()
        if success and target_user:
            display = target_user.username or target_user.first_name or f"ID: {target_user.id}"
            await callback_query.answer(f"⏭ Вы пропустили @{display} вперед")
            await process_queue_updates(bot, queue_id, session)
        else:
            await callback_query.answer(message_text, show_alert=True)

    elif command == "swap":
        await callback_query.answer()
        return await swap_entries_handler(callback_query, state, user_id, queue_id, session=session, bot=bot)

    # In private chat, also edit the current message immediately
    if not in_group:
        updated_queue = await get_queue_with_data(session, queue_id)
        if updated_queue:
            text, keyboard = await generate_queue_message(
                user_id=user_id,
                queue=updated_queue,
                in_group=False,
                bot=bot
            )
            try:
                await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except TelegramBadRequest:
                pass


@router.callback_query(F.data.startswith("queue_admin:"))
async def queue_admin_handler(callback_query: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession):
    method = callback_query.data.split(":")[1]
    queue_id = int(callback_query.data.split(":")[2])
    user_id = callback_query.from_user.id

    if method == "settings":
        await queue_settings_handler(callback_query)
        await callback_query.answer()
    elif method == "delete":
        await queue_delete_handler(callback_query, state, session=session)
    elif method == "rename":
        await queue_rename_handler(callback_query, state)
        await callback_query.answer()
    elif method == "clear":
        await queue_clear_handler(callback_query, session=session)
        await callback_query.answer()
    elif method == "move":
        success, msg, queue = await atomic_move_queue(session, queue_id, user_id)
        await session.commit()
        await callback_query.answer(msg, show_alert=not success)
        if success:
            await process_queue_updates(bot, queue_id, session)
            if callback_query.message.chat.type not in ["group", "supergroup"]:
                text, keyboard = await generate_queue_message(user_id, queue, False, bot)
                try:
                    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_action_handler(callback_query: CallbackQuery, bot: Bot, session: AsyncSession):
    parts = callback_query.data.split(":")
    target_id = int(parts[1])
    action = parts[2]
    user_id = callback_query.from_user.id

    if action == "clear":
        success, msg = await atomic_clear_queue(session, target_id, user_id)
        await session.commit()
        await callback_query.answer(msg, show_alert=not success)
        if success:
            await open_queue_callback(callback_query, bot, session, target_id)
            await process_queue_updates(bot, target_id, session)


@router.callback_query(F.data.startswith("back:"))
async def cancel_action_handler(callback_query: CallbackQuery, bot: Bot, session: AsyncSession):
    parts = callback_query.data.split(":")
    target_id = int(parts[1])
    action = parts[2]

    if action == "clear":
        await open_queue_callback(callback_query, bot, session, target_id)


async def queue_clear_handler(callback_query: CallbackQuery, session: AsyncSession):
    queue_id = int(callback_query.data.split(":")[2])
    queue = await get_queue_by_id(queue_id, session=session)
    if not queue:
        return await callback_query.answer("⚠️ Очередь не существует", show_alert=True)

    await callback_query.message.edit_text(
        text=f"❓ Очистить очередь <b>{safe_html(queue.name)}</b>?",
        reply_markup=InlineKeyboardBuilderFactory.create_confirmation_keyboard("clear", queue_id),
        parse_mode="HTML"
    )


async def queue_settings_handler(callback_query: CallbackQuery):
    queue_id = int(callback_query.data.split(":")[2])
    keyboard = InlineKeyboardBuilderFactory.queue_settings_keyboard(queue_id)
    return await callback_query.message.edit_text(
        "🛠 Выберите действие",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def queue_delete_handler(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    from handlers.rooms import queue_callback

    queue_id = int(callback_query.data.split(":")[2])
    room_id = await delete_queue(queue_id, session=session)
    await session.commit()

    if not room_id:
        return await callback_query.message.delete()

    await callback_query.answer("🗑 Очередь удалена")
    return await queue_callback(callback_query, session=session, room_id=room_id)


@router.message(CreateQueueState.waiting_for_queue_rename)
async def queue_rename_text_handler(message: Message, state: FSMContext, session: AsyncSession):
    queue_name = message.text
    data = await state.get_data()
    queue_id = data.get("queue_id")

    if not queue_name or queue_name == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Переименование очереди отменено",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    queue = await session.get(QueueModel, queue_id)
    if not queue:
        await state.clear()
        return await message.answer(
            "⚠️ Очередь не существует",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    queue.name = queue_name.strip()
    await session.commit()
    await state.clear()

    return await message.answer(
        f"✅ Очередь успешно переименована в <b>{safe_html(queue.name)}</b>!",
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


async def swap_entries_handler(callback_query: CallbackQuery, state: FSMContext, user_id: int, queue_id: int, session: AsyncSession, bot: Bot):
    active_swap_request = await has_active_swap_request(queue_id, user_id, session=session)
    if active_swap_request:
        if active_swap_request.target_id == user_id:
            sender_name = get_user_display_name(active_swap_request.sender)
            entry_from = await get_entry_with_user_by_user_id(queue_id, active_swap_request.sender_id, session=session)
            entry_to = await get_entry_with_user_by_user_id(queue_id, active_swap_request.target_id, session=session)

            text = (
                f"🤝 У Вас уже есть открытый запрос на смену позиции в очереди <b>{safe_html(active_swap_request.queue.name)}</b>\n"
                f"Ответьте на запрос от пользователя {sender_name} или дождитесь отмены\n\n"
                f"✨ <b>Запрос поменяться местами</b>\n"
                f"Очередь: <b>{safe_html(entry_from.queue.name if entry_from else '')}</b>\n"
                f"Пользователь {sender_name} (место {entry_from.position if entry_from else '?'}) хочет поменяться с Вами (место {entry_to.position if entry_to else '?'})"
            )
            return await callback_query.message.answer(
                text=text,
                reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(active_swap_request.id),
                parse_mode="HTML"
            )

        elif active_swap_request.sender_id == user_id:
            target_name = get_user_display_name(active_swap_request.target)
            text = (
                f"⏳ У Вас уже есть открытый запрос на смену позиции в очереди <b>{safe_html(active_swap_request.queue.name)}</b>\n"
                f"Дождитесь ответа от {target_name} или отмените его"
            )
            return await callback_query.message.answer(
                text=text,
                reply_markup=InlineKeyboardBuilderFactory().create_cancel_swap_request_keyboard(active_swap_request.id),
                parse_mode="HTML"
            )

    await state.set_state(SwapEntriesState.waiting_postition)
    await state.update_data(queue_id=queue_id)
    return await callback_query.message.answer(
        "🔢 Введите номер позиции, на которую Вы хотите встать:",
        reply_markup=ReplyKeyboardBuilderFactory().create_cancel_swap_keyboard()
    )


@router.callback_query(F.data.startswith("cancel_request:"))
async def cancel_swap_request_inline_handler(callback_query: CallbackQuery, session: AsyncSession):
    swap_request_id = int(callback_query.data.split(":")[1])
    success = await delete_swap_request(swap_request_id, session=session)
    await session.commit()

    if not success:
        await callback_query.answer("Запрос не найден или уже обработан")
    else:
        await callback_query.answer("❌ Запрос отменён")

    try:
        await callback_query.message.delete()
    except Exception:
        pass


@router.message(SwapEntriesState.waiting_postition)
async def process_position_message(message: Message, state: FSMContext, session: AsyncSession):
    position_text = message.text
    if not position_text or position_text == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Смена позиции отменена",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    try:
        position = int(position_text.strip())
    except ValueError:
        return await message.answer("⚠️ Неправильный формат позиции. Введите целое число:")

    data = await state.get_data()
    queue_id = data.get("queue_id")
    if not queue_id:
        await state.clear()
        return await message.answer("⚠️ Сессия истекла. Начните выбор позиции заново.")

    user_id = message.from_user.id
    entry = await get_entry_by_position(queue_id, position, user_id, session=session)
    if entry is None:
        return await message.answer("⚠️ На этой позиции никого нет или это ваше место. Повторите попытку:")

    await state.set_state(SwapEntriesState.waiting_comment)
    await state.update_data(target_user_id=entry.user_id, target_pos=position)
    target_display = get_user_display_name(entry.user)

    return await message.answer(
        f"🎯 Вы выбрали место <b>№{position}. {target_display}</b>\n💬 Введите комментарий (опционально):",
        reply_markup=ReplyKeyboardBuilderFactory.create_writing_comment_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == WritingCommentButtons.NO_COMMENT.value, SwapEntriesState.waiting_comment)
async def send_swap_offer_no_comment(message: Message, state: FSMContext, bot: Bot, session: AsyncSession):
    await send_swap_offer_impl(message, state, bot, session, comment=None)


@router.message(SwapEntriesState.waiting_comment)
async def send_comment_handler(message: Message, state: FSMContext, bot: Bot, session: AsyncSession):
    if message.text == MainMenuButtons.CANCEL.value:
        await state.clear()
        return await message.answer(
            "❌ Отмена запроса на смену позиции",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )
    await send_swap_offer_impl(message, state, bot, session, comment=message.text)


async def send_swap_offer_impl(message: Message, state: FSMContext, bot: Bot, session: AsyncSession, comment: str | None = None):
    data = await state.get_data()
    queue_id = data.get("queue_id")
    target_pos = data.get("target_pos")
    target_user_id = data.get("target_user_id")
    user_id_from = message.from_user.id

    entry = await get_entry_by_user_id(queue_id, user_id_from, session=session)
    if not entry:
        await state.clear()
        return await message.answer("❌ Вы не состоите в этой очереди", reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard())

    sender_user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        session=session
    )
    sender_display = get_user_display_name(sender_user)

    swap_request = await create_swap_request(queue_id, user_id_from, target_user_id, session=session)
    if swap_request is None:
        await state.clear()
        return await message.answer("❌ У вас уже есть активный запрос на обмен в этой очереди", reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard())

    await session.commit()
    await state.clear()

    text = (
        f"🤝 <b>Запрос поменяться местами</b>\n"
        f"Очередь: <b>{safe_html(entry.queue.name)}</b>\n"
        f"Пользователь {sender_display} (место {entry.position}) хочет поменяться с Вами (место {target_pos})"
    )
    if comment:
        text += f"\n\n💬 Комментарий: <i>{safe_html(comment)}</i>"

    try:
        await bot.send_message(
            chat_id=target_user_id, 
            text=text,
            reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(swap_request.id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Failed to send swap offer in private chat to {target_user_id}: {e}")

    return await message.answer(
        f"📨 Предложение встать на место {target_pos} отправлено!",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("swap:"))
async def swap_offer_handle(callback_query: CallbackQuery, bot: Bot, session: AsyncSession):
    parts = callback_query.data.split(":")
    action = parts[1]
    request_id = int(parts[2])
    target_id = callback_query.from_user.id

    request = await get_swap_request_by_id(request_id, session=session)
    if not request:
        await callback_query.answer("⚠️ Запрос уже недействителен или отменен", show_alert=True)
        return await callback_query.message.delete()

    if request.target_id != target_id:
        return await callback_query.answer("🚫 Это предложение адресовано не вам", show_alert=True)

    if action == "accept":
        success, msg, sender_entry, target_entry, queue_name = await confirm_and_execute_swap(
            session=session,
            request_id=request_id,
            target_id=target_id
        )
        await session.commit()

        if success:
            await callback_query.message.edit_text(
                f"✅ Обмен выполнен! Очередь <b>{safe_html(queue_name)}</b>\nВаше новое место: {target_entry.position}",
                parse_mode="HTML"
            )
            try:
                await bot.send_message(
                    request.sender_id,
                    f"✅ Обмен принят!\nОчередь <b>{safe_html(queue_name)}</b>\nВаше новое место: {sender_entry.position}",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await process_queue_updates(bot, request.queue_id, session)
        else:
            await callback_query.answer(msg, show_alert=True)
            try:
                await callback_query.message.delete()
            except Exception:
                pass

    elif action == "decline":
        await delete_swap_request(request_id, session=session)
        await session.commit()
        await callback_query.message.edit_text("❌ Вы отклонили запрос на обмен")
        target_display = get_user_display_name(request.target)
        try:
            await bot.send_message(
                request.sender_id,
                f"❌ Пользователь {target_display} отклонил предложение обмена",
                parse_mode="HTML"
            )
        except Exception:
            pass


@router.message(Command("queue"), ChatAdminFilter())
async def send_queue_message_command(message: Message, session: AsyncSession):
    room = await get_room_by_chat_id(message.chat.id, session=session)
    if not room:
        return await message.answer(
            "⚠️ Эта группа не привязана ни к одной комнате.\nИспользуйте команду <code>/bind [код_комнаты]</code>",
            parse_mode="HTML"
        )

    queues = room.queues
    if not queues:
        return await message.answer(
            f"📋 В комнате <b>{safe_html(room.name)}</b> пока нет очередей.\nСоздайте очередь через меню бота в личных сообщениях.",
            parse_mode="HTML"
        )

    buttons = [(queue.name, f"open_queue:{queue.id}:{message.chat.id}") for queue in queues]
    
    return await message.answer(
        f"📋 Очереди в комнате <b>{safe_html(room.name)}</b>:",
        reply_markup=InlineKeyboardBuilderFactory().build_inline_keyboard(
            buttons, 
            adjust=[2] * len(queues)
        ),
        parse_mode="HTML"
    )


async def process_queue_updates(bot: Bot, queue_id: int, session: AsyncSession):
    """
    Schedules debounced update for live queue message and checks notifications
    for speaker (#1) and next (#2).
    """
    schedule_live_queue_update(bot, queue_id)

    queue = await get_queue_by_id(queue_id, session=session)
    if not queue:
        return

    if not queue.entries:
        queue.current_speaker_id = None
        queue.notified_next_id = None
        await session.flush()
        return

    # 1. Check speaker notification (#1)
    first_student = await get_entry_by_position(queue_id, 1, session=session)
    if first_student and queue.current_speaker_id != first_student.user_id:
        queue.current_speaker_id = first_student.user_id
        await session.flush()
        try:
            await bot.send_message(
                chat_id=first_student.user_id,
                text=f"🔔 <b>Твоё время пришло!</b>\nПора отвечать в очереди <b>{safe_html(queue.name)}</b>. Удачи!",
                parse_mode="HTML"
            )
        except Exception:
            if queue.last_chat_id:
                first_display = get_user_display_name(first_student.user)
                try:
                    await bot.send_message(
                        chat_id=queue.last_chat_id,
                        text=f"📢 {first_display}, твоя очередь в списке «{safe_html(queue.name)}»!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

    # 2. Check next notification (#2)
    second_student = await get_entry_by_position(queue_id, 2, session=session)
    if not second_student:
        queue.notified_next_id = None
        await session.flush()
    elif queue.notified_next_id != second_student.user_id:
        queue.notified_next_id = second_student.user_id
        await session.flush()
        try:
            await bot.send_message(
                chat_id=second_student.user_id,
                text=f"🔜 <b>Приготовься, ты следующий!</b>\n\nГотовься отвечать в очереди <b>{safe_html(queue.name)}</b>",
                parse_mode="HTML"
            )
        except Exception:
            if queue.last_chat_id:
                second_display = get_user_display_name(second_student.user)
                try:
                    await bot.send_message(
                        chat_id=queue.last_chat_id,
                        text=f"🏃 {second_display}, готовься, ты следующий в очереди «{safe_html(queue.name)}»!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
