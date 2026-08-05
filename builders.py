from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from database.models import UserRole
from enums import MainMenuButtons


class ReplyKeyboardBuilderFactory:
    @staticmethod
    def build_keyboard(buttons: list[str], adjust: list[int] | None = None) -> ReplyKeyboardBuilder:
        builder = ReplyKeyboardBuilder()

        builder.add(*(KeyboardButton(text=btn) for btn in buttons))
    
        if adjust:
            builder.adjust(*adjust)
        return builder.as_markup(resize_keyboard=True)

    @staticmethod
    def create_main_menu_keyboard() -> ReplyKeyboardBuilder:
        buttons = [MainMenuButtons.CREATE_ROOM.value, MainMenuButtons.JOIN_ROOM.value, MainMenuButtons.USER_ROOMS.value]
        return ReplyKeyboardBuilderFactory().build_keyboard(buttons, adjust=[1, 1, 1])


class InlineKeyboardBuilderFactory:
    @staticmethod
    def build_inline_keyboard(buttons: list[tuple[str, str]], adjust: list[int] | None = None) -> InlineKeyboardBuilder:
        builder = InlineKeyboardBuilder()

        builder.add(*(InlineKeyboardButton(text=btn[0], callback_data=btn[1]) for btn in buttons))
    
        if adjust:
            builder.adjust(*adjust)
        return builder.as_markup(resize_keyboard=True)

    @staticmethod
    def room_inline_keyboard(user_role: str, room_id: int | None = None):
        buttons = []
        if user_role == UserRole.ADMIN.value:
            buttons.append(("Создать очередь", f"room_settings:create_queue:{room_id}"))
            buttons.append(("Настройки", f"room_settings:settings:{room_id}"))
            buttons.append(("Участники", f"room_settings:members:{room_id}"))

        buttons.append(("Очереди", f"queue:{room_id}"))
        buttons.append(("Выйти из комнаты", f"leave_room:{room_id}"))
        buttons.append(("Назад", "back"))
        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[2, 2, 1])
