from builders import ReplyKeyboardBuilderFactory
from accessors.users import get_or_create_user
from aiogram.filters import Command


async def start_command(message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"Hello, @{user.username or 'User'}! Your ID is {user.id}.",
        reply_markup=ReplyKeyboardBuilderFactory().create_main_menu_keyboard()
    )


# Note: registration with Dispatcher is done in main.py via decorator wrappers there.
