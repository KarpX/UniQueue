import logging

from aiogram import F, Bot, Router
from aiogram.types import CallbackQuery, Message

from accessors.queues import get_entry_by_user_id, get_entry_with_user_by_user_id, get_queue_by_id, has_active_swap_request
from accessors.rooms import change_member_role, delete_room_member, get_room_member_by_user_id
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.users import get_or_create_user
from aiogram.filters import CommandObject, CommandStart
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
                    reply_markup=InlineKeyboardBuilderFactory().create_acceptance_swap_keyboard(queue_id, entry_to.position, entry_from.position),
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
    
    return await message.answer(
        "Привет! Я бот для управления очередями. Войдите в комнату по ссылке или коду.",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )

@router.callback_query(F.data.startswith("mem:view"))
async def view_member_hanlde(callback_query: CallbackQuery):
    _, _, room_id, user_id, page = callback_query.data.split(":")
    room_id = int(room_id)
    user_id = int(user_id)
    page = int(page)

    user = await get_or_create_user(user_id)
    member = await get_room_member_by_user_id(room_id, user_id)
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
            f"👤 <b>Карточка участника 2s</b>\n\n"
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
