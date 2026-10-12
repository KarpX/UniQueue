import logging
from aiogram import F, Bot, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from accessors.queues import (
    ensure_user_in_room,
    get_entry_by_user_id,
    get_entry_with_user_by_user_id,
    get_queue_by_id,
    has_active_swap_request,
)
from accessors.rooms import (
    change_member_role,
    delete_room_member,
    get_room_member_by_user_id,
)
from accessors.users import get_or_create_user
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from database.models import UserRole
from handlers.filters.admin_filter import ChatAdminFilter
from handlers.queues import generate_queue_message
from handlers.rooms import join_room_by_link, room_settings_callback
from states import SwapEntriesState
from utils import get_user_display_name, safe_html

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart(deep_link=True))
async def start_command(message: Message, command: CommandObject, state: FSMContext, bot: Bot, session: AsyncSession):
    args = command.args
    from_user = message.from_user
    user = await get_or_create_user(
        user_id=from_user.id,
        username=from_user.username,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
        session=session
    )
    await session.commit()

    if args.startswith("swap_"):
        try:
            queue_id = int(args.split("_")[1])
        except (IndexError, ValueError):
            return await message.answer("⚠️ Неверная ссылка для обмена")

        user_id = message.from_user.id
        queue = await get_queue_by_id(queue_id, session=session)
        if not queue:
            return await message.answer("⚠️ Очередь не найдена")

        await ensure_user_in_room(
            session=session,
            user_id=user_id,
            username=from_user.username,
            first_name=from_user.first_name,
            last_name=from_user.last_name,
            room_id=queue.room_id
        )

        queue_message, _ = await generate_queue_message(user_id, queue, bot=bot)

        active_swap_request = await has_active_swap_request(queue_id, user_id, session=session)
        if active_swap_request:
            if active_swap_request.target_id == user_id:
                sender_display = get_user_display_name(active_swap_request.sender)
                entry_from = await get_entry_with_user_by_user_id(queue_id, active_swap_request.sender_id, session=session)
                entry_to = await get_entry_with_user_by_user_id(queue_id, active_swap_request.target_id, session=session)

                text = (
                    f"У Вас уже есть открытый запрос на смену позиции в очереди <b>{safe_html(active_swap_request.queue.name)}</b>\n"
                    f"Ответьте на запрос от пользователя {sender_display} или дождитесь отмены\n\n"
                    f"<b>Запрос поменяться местами</b>\n"
                    f"Очередь: <b>{safe_html(entry_from.queue.name if entry_from else '')}</b>\n"
                    f"Пользователь {sender_display} (место {entry_from.position if entry_from else '?'}) хочет поменяться с Вами (место {entry_to.position if entry_to else '?'})"
                )
                return await message.answer(
                    text=text,
                    reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(active_swap_request.id),
                    parse_mode="HTML"
                )

            elif active_swap_request.sender_id == user_id:
                target_display = get_user_display_name(active_swap_request.target)
                text = (
                    f"У Вас уже есть открытый запрос на смену позиции в очереди <b>{safe_html(active_swap_request.queue.name)}</b>\n"
                    f"Дождитесь ответа от {target_display} или отмените его"
                )
                return await message.answer(
                    text=text,
                    reply_markup=InlineKeyboardBuilderFactory().create_cancel_swap_request_keyboard(active_swap_request.id),
                    parse_mode="HTML"
                )

        entry = await get_entry_by_user_id(queue_id, user_id, session=session)
        if not entry:
            return await message.answer("❌ Вы не состоите в этой очереди")

        await state.update_data(queue_id=queue_id, user_pos=entry.position)
        await state.set_state(SwapEntriesState.waiting_postition)

        return await message.answer(
            queue_message + "\n\nВведите номер позиции, на которую Вы хотите встать:",
            reply_markup=ReplyKeyboardBuilderFactory().create_cancel_swap_keyboard(),
            parse_mode="HTML"
        )

    invite_code = args.strip().upper()
    await join_room_by_link(message, user.id, invite_code, session=session)


@router.message(CommandStart(), ChatAdminFilter())
async def cmd_start_common(message: Message, session: AsyncSession):
    from_user = message.from_user
    user = await get_or_create_user(
        user_id=from_user.id,
        username=from_user.username,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
        session=session
    )
    await session.commit()

    user_name = user.first_name or user.username or "студент"
    welcome_text = (
        f"👋 <b>Привет, {safe_html(user_name)}!</b>\n\n"
        f"Я — <b>UniQueue</b>, твой помощник в организации учебных очередей. "
        f"Больше не нужно искать списки в бесконечных переписках чата!\n\n"
        f"<b>Что я умею:</b>\n"
        f"🏫 <b>Комнаты:</b> Создавай отдельные пространства для каждой группы.\n"
        f"📝 <b>Очереди:</b> Записывайся на сдачу лаб или зачетов одним нажатием кнопки.\n"
        f"🤝 <b>Обмен:</b> Предлагай одногруппникам поменяться местами, если не успеваешь.\n"
        f"🔔 <b>Уведомления:</b> Я пришлю тебе сообщение в личку, когда твоя очередь будет подходить.\n"
        f"👥 <b>Интеграция:</b> Меня можно добавить в чат вашей группы, чтобы список всегда был перед глазами. Подробнее – /help\n\n"
        f"Чтобы начать, используй кнопки меню ниже: <b>вступи в комнату</b> по коду от старосты или <b>создай свою</b>! 👇"
    )

    if message.chat.type == "private":
        return await message.answer(
            welcome_text,
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
            parse_mode="HTML"
        )

    return await message.answer(welcome_text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help_text(message: Message):
    help_text = (
        "🤖 <b>UniQueue: Инструкция по работе в группе</b>\n\n"
        "Я помогу вам забыть о хаосе со списками в чате. Вот как правильно со мной работать:\n\n"
        "🛠 <b>Для старост и администраторов:</b>\n"
        "1. <b>Привязка чата:</b> Используйте команду <code>/bind [код_комнаты]</code>. "
        "Ваш персональный код можно найти в настройках комнаты в личных сообщениях бота.\n"
        "Отвязать чат можно командой <code>/unbind</code>.\n"
        "2. <b>Вызов списка:</b> Команда <code>/queue</code> выводит доступные очереди.\n"
        "3. <b>Совет:</b> 📌 <b>Закрепите</b> сообщение с очередью. Я буду автоматически обновлять "
        "список в этом сообщении при каждом изменении, чтобы вам не пришлось скроллить чат.\n\n"
        "📝 <b>Как пользоваться студентам:</b>\n"
        "• <b>Записаться</b> — встать в конец списка.\n"
        "• <b>🏃 Выйти</b> — выйти из очереди.\n"
        "• <b>🤝 Поменяться</b> — предложить любому участнику обменяться местами.\n"
        "• <b>⏭ Пропустить</b> — быстрая рокировка со следующим после вас человеком.\n"
        "• <b>↕️ Сдвинуть</b> — передвинуть очередь на одного человека вперед.\n\n"
        "💡 <b>Важные правила:</b>\n"
        "• <b>Личка — это пульт:</b> Чтобы я мог прислать вам уведомление <i>«Твоя очередь подошла!»</i>, "
        "вы <b>обязательно</b> должны запустить меня в личных сообщениях.\n"
        "• <b>Без спама:</b> Одно сообщение с очередью работает для всех. Не нужно вызывать его многократно.\n\n"
        "<i>Желаю быстрых сдач и отсутствия «хвостов»! 🚀</i>"
    )

    if message.chat.type == "private":
        return await message.answer(
            help_text,
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard(),
            parse_mode="HTML"
        )

    return await message.answer(help_text, parse_mode="HTML")


@router.callback_query(F.data.startswith("mem:view"))
async def view_member_handle(callback_query: CallbackQuery, session: AsyncSession):
    parts = callback_query.data.split(":")
    room_id = int(parts[2])
    user_id = int(parts[3])
    page = int(parts[4])

    user = await get_or_create_user(user_id, session=session)
    member = await get_room_member_by_user_id(room_id, user_id, session=session)
    if not member:
        return await callback_query.answer("⚠️ Участник не найден в этой комнате", show_alert=True)

    await callback_query.answer()
    display_name = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"
    return await callback_query.message.edit_text(
        text=(
            f"👤 <b>Карточка участника</b>\n\n"
            f"Имя: {safe_html(display_name)}\n"
            f"ID: <code>{user_id}</code>\n"
            f"Роль: {'Администратор 👑' if member.role == UserRole.ADMIN else 'Участник 👤'}"
        ),
        reply_markup=InlineKeyboardBuilderFactory.member_settings_keyboard(room_id, user_id, member.role),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("member_settings:"))
async def member_settings_handle(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback_query.data.split(":")
    action = parts[1]
    room_id = int(parts[2])
    target_user_id = int(parts[3])
    caller_id = callback_query.from_user.id

    # Check caller is ADMIN in this room
    caller_member = await get_room_member_by_user_id(room_id, caller_id, session=session)
    if not caller_member or caller_member.role != UserRole.ADMIN:
        return await callback_query.answer("🚫 У вас нет прав для управления участниками", show_alert=True)

    if action == "make_admin":
        await change_role(callback_query, room_id, target_user_id, UserRole.ADMIN, session=session)
    elif action == "make_member":
        await change_role(callback_query, room_id, target_user_id, UserRole.MEMBER, session=session)
    elif action == "kick_member":
        await kick_member(callback_query, room_id, target_user_id, state, session=session)


async def change_role(callback_query: CallbackQuery, room_id: int, user_id: int, role: UserRole, session: AsyncSession):
    success = await change_member_role(room_id, user_id, role, session=session)
    await session.commit()

    if not success:
        return await callback_query.answer("❌ Ошибка при изменении роли (нельзя разжаловать создателя)", show_alert=True)

    user = await get_or_create_user(user_id, session=session)
    member = await get_room_member_by_user_id(room_id, user_id, session=session)

    display_name = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"
    await callback_query.message.edit_text(
        text=(
            f"👤 <b>Карточка участника</b>\n\n"
            f"Имя: {safe_html(display_name)}\n"
            f"ID: <code>{user_id}</code>\n"
            f"Роль: {'Администратор 👑' if member.role == UserRole.ADMIN else 'Участник 👤'}"
        ),
        reply_markup=InlineKeyboardBuilderFactory.member_settings_keyboard(room_id, user_id, member.role),
        parse_mode="HTML"
    )
    return await callback_query.answer("Роль изменена")


async def kick_member(callback_query: CallbackQuery, room_id: int, user_id: int, state: FSMContext, session: AsyncSession):
    success = await delete_room_member(room_id, user_id, session=session)
    await session.commit()

    if not success:
        return await callback_query.answer("❌ Нельзя исключить создателя комнаты", show_alert=True)
    
    await room_settings_callback(callback_query, state, session=session)
    return await callback_query.answer("Участник исключен")
