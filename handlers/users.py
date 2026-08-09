from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from accessors.rooms import change_member_role, delete_room_member, get_room_member_by_user_id
from builders import InlineKeyboardBuilderFactory, ReplyKeyboardBuilderFactory
from accessors.users import get_or_create_user
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from database.models import UserRole
from handlers.rooms import join_room_by_link, room_settings_callback


router = Router()

@router.message(Command("start"))
async def start_command(message: Message, command: CommandObject):
    args = command.args
    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    if not args:
        return await message.answer(
           "Привет! Я бот для управления очередями. Войдите в комнату по ссылке или коду.",
            reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
        )

    invite_code = args.strip().upper()

    await join_room_by_link(message, user.id, invite_code)


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
