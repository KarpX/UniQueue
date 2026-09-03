import logging

from aiogram import F, Bot, Router
from aiogram.types import CallbackQuery, Message

from accessors.queues import get_entry_by_user_id, get_entry_with_user_by_user_id, get_queue_by_id, has_active_swap_request
from accessors.rooms import change_member_role, delete_room_member, get_room_member_by_user_id
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.users import get_or_create_user
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext

from database.models import UserRole
from handlers.filters.admin_filter import ChatAdminFilter
from handlers.queues import generate_queue_message
from handlers.rooms import join_room_by_link, room_settings_callback
from states import SwapEntriesState


logger = logging.getLogger(__name__)

router = Router()

@router.message(CommandStart(deep_link=True))
async def start_command(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    args = command.args
    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    if args.startswith("swap_"):
        queue_id = int(args.split("_")[1])
        user_id = message.from_user.id

        queue = await get_queue_by_id(queue_id)

        queue_message, _ = await generate_queue_message(user_id, queue, bot=bot)

        active_swap_request = await has_active_swap_request(queue_id, user_id)
        if active_swap_request:
            if active_swap_request.target_id == user_id:
                text = (f"У Вас уже есть открытый запрос на смену позиции в очереди <b>{active_swap_request.queue.name}</b>\n"
                    f"Ответьте на запрос от пользователя @{active_swap_request.sender.username} или дождитесь отмены")
    
                entry_from = await get_entry_with_user_by_user_id(queue_id, active_swap_request.sender_id)
    
                entry_to = await get_entry_with_user_by_user_id(queue_id, active_swap_request.target_id)
    
                text += (f"\n\n<b>Запрос поменяться местами</b>\n"
                            f"Очередь <b>{entry_from.queue.name}</b>\n"
                            f"Пользователь @{entry_from.user.username} (место {entry_from.position}) хочет поменяться с Вами (место {entry_to.position})")
    
                return await message.answer(
                    text=text,
                    reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(active_swap_request.id),
                    parse_mode="HTML"
                )
    
            elif active_swap_request.sender_id == user_id:
                text = (f"У Вас уже есть открытый запрос на смену позиции в очереди <b>{active_swap_request.queue.name}</b>\n"
                    f"Дождитесь ответа от @{active_swap_request.target.username} или отмените его")
                return await message.answer(
                    text=text,
                    reply_markup=InlineKeyboardBuilderFactory().create_cancel_swap_request_keyboard(active_swap_request.id),
                    parse_mode="HTML"
                )

        entry = await get_entry_by_user_id(queue_id, user_id)
        if not entry:
            return await message.answer("❌ Вы не состоите в этой очереди")

        await state.update_data(queue_id=queue_id, user_pos=entry.position)
        await state.set_state(SwapEntriesState.waiting_postition)

        text = queue_message

        await message.answer(
            text + "\n\nВведите номер позиции, на которую Вы хотите встать",
            reply_markup=ReplyKeyboardBuilderFactory().create_cancel_swap_keyboard(),
            parse_mode="HTML"
        )
        return

    invite_code = args.strip().upper()

    await join_room_by_link(message, user.id, invite_code)

@router.message(CommandStart(), ChatAdminFilter())
async def cmd_start_common(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    welcome_text = (
        f"👋 <b>Привет, @{user.username}!</b>\n\n"
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

    return await message.answer(
        welcome_text,
        parse_mode="HTML"
    )

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
    "3. <b>Совет:</b> 📌 <b>Закрепите</b>* сообщение с очередью. Я буду автоматически обновлять "
    "список в этом сообщении при каждом изменении, чтобы вам не пришлось скроллить чат.\n"
    "* Если у меня будут права администратора в группе, то я смогу самостоятельно закреплять сообщения\n\n"
    
    "📝 <b>Как пользоваться студентам:</b>\n"
    "• <b>Записаться</b> — встать в конец списка.\n"
    "• <b>🏃 Выйти</b> — выйти из очереди.\n"
    "• <b>🤝 Поменяться</b> — я перенесу вас в личные сообщения, где вы сможете предложить "
    "любому участнику обменяться местами и написать причину.\n"
    "• <b>⏭ Пропустить</b> — быстрая рокировка со следующим после вас человеком.\n"
    "• <b>↕️ Сдвинуть</b> – Передвинуть очередь на одного человека вперёд (могут только администраторы или первый человек в очереди)\n\n"
    
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

    return await message.answer(
        help_text,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("mem:view"))
async def view_member_hanlde(callback_query: CallbackQuery):
    _, _, room_id, user_id, page = callback_query.data.split(":")
    room_id = int(room_id)
    user_id = int(user_id)
    page = int(page)

    user = await get_or_create_user(user_id)
    member = await get_room_member_by_user_id(room_id, user_id)
    await callback_query.answer()
    return await callback_query.message.edit_text(
        text=(
        f"👤 <b>Карточка участника</b>\n\n"
        f"Имя: @{user.username or 'Скрыто'}\n"
        f"ID: {user_id}\n"
        f"Роль: {'Администратор 👑' if member.role == UserRole.ADMIN else 'Участник 👤'}"
        ),
        reply_markup=InlineKeyboardBuilderFactory.member_settings_keyboard(room_id, user_id, member.role),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("member_settings:"))
async def member_settings_handle(callback_query: CallbackQuery, state: FSMContext):
    _, action, room_id, user_id = callback_query.data.split(":")
    room_id = int(room_id)
    user_id = int(user_id)

    if action == "make_admin":
        await change_role(callback_query, room_id, user_id, UserRole.ADMIN)
    elif action == "make_member":
        await change_role(callback_query, room_id, user_id, UserRole.MEMBER)
    elif action == "kick_member":
        await kick_member(callback_query, room_id, user_id, state)


async def change_role(callback_query: CallbackQuery, room_id: int, user_id: int, role):
    success = await change_member_role(room_id, user_id, role)

    if not success:
        return await callback_query.answer("Ошибка при изменении роли")

    user = await get_or_create_user(user_id)
    member = await get_room_member_by_user_id(room_id, user_id)

    await callback_query.message.edit_text(
            text=(
            f"👤 <b>Карточка участника</b>\n\n"
            f"Имя: @{user.username or 'Скрыто'}\n"
            f"ID: {user_id}\n"
            f"Роль: {'Администратор 👑' if member.role == UserRole.ADMIN else 'Участник 👤'}"
            ),
            reply_markup=InlineKeyboardBuilderFactory.member_settings_keyboard(room_id, user_id, member.role),
            parse_mode="HTML"
        )

    return await callback_query.answer("Роль изменена")

async def kick_member(callback_query: CallbackQuery, room_id: int, user_id: int, state: FSMContext):
    success = await delete_room_member(room_id, user_id)

    if not success:
        return await callback_query.answer("Ошибка изгнания участника")
    
    await room_settings_callback(callback_query, state)
    return await callback_query.answer("Участник выгнан")

# Note: registration with Dispatcher is done in main.py via decorator wrappers there.
