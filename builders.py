from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from database.models import UserRole
from enums import MainMenuButtons, QueueInlineButtons, RoomInlineButtons


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
            buttons.append((RoomInlineButtons.CREATE_QUEUE.value, f"room_settings:create_queue:{room_id}"))
            buttons.append((RoomInlineButtons.SETTINGS_ROOM.value, f"room_settings:settings:{room_id}"))
            buttons.append((RoomInlineButtons.MEMBERS.value, f"room_settings:members:{room_id}"))

        buttons.append((RoomInlineButtons.QUEUES_LIST.value, f"queue:{room_id}"))
        buttons.append((RoomInlineButtons.LEAVE_ROOM.value, f"leave_room:{room_id}"))
        buttons.append((RoomInlineButtons.BACK_ROOM.value, "back"))
        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[2, 2, 1])

    @staticmethod
    def queue_inline_keyboard(user_id: int, is_in_queue: bool, room_members_admin_ids: list[int], queue_id: int, room_id: int):
        buttons = []
        if not is_in_queue:
            buttons.append((QueueInlineButtons.JOIN_QUEUE.value, f"queue_control:join:{queue_id}"))
        else:
            buttons.append((QueueInlineButtons.EXIT_QUEUE.value, f"queue_control:exit:{queue_id}"))
    
        buttons.append((QueueInlineButtons.SKIP_QUEUE.value, f"queue_control:skip:{queue_id}"))
    
        if user_id in room_members_admin_ids:
            buttons.append((QueueInlineButtons.SETTINGS_QUEUE.value, f"queue_admin:settings:{queue_id}"))
    
        buttons.append((QueueInlineButtons.BACK_QUEUE.value, f"queue:{room_id}"))

        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[2, 1, 1])

    @staticmethod
    def queue_settings_keyboard(queue_id: int):
        buttons = [
            (QueueInlineButtons.RENAME_QUEUE.value, f"queue_admin:rename:{queue_id}"),
            (QueueInlineButtons.DELETE_QUEUE.value, f"queue_admin:delete:{queue_id}"),
            (QueueInlineButtons.BACK_QUEUE.value, f"queue_back:{queue_id}")
        ]

        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[1, 1])


