from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from database.models import UserRole
from enums import MainMenuButtons, MemberInlineButtons, QueueInlineButtons, RoomInlineButtons, RoomSettingsButtons, SwapInlineButtons, WritingCommentButtons


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

    @staticmethod
    def create_writing_comment_keyboard():
        buttons = [WritingCommentButtons.NO_COMMENT.value]
        return ReplyKeyboardBuilderFactory.build_keyboard(buttons, adjust=[1])

    @staticmethod
    def create_cancel_swap_keyboard():
        buttons = [MainMenuButtons.CANCEL.value]
        return ReplyKeyboardBuilderFactory().build_keyboard(buttons)


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
        buttons.append((QueueInlineButtons.SWAP_QUEUE.value, f"queue_control:swap:{queue_id}:{user_id}"))
    
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

    @staticmethod
    def room_members_keyboard(room_members, room_id: int, page: int, total_count: int, items_per_page: int):
        buttons = []
        adjust = []

        for member in room_members:
            name_label = member.user.username or f"ID: {member.user_id}"
            role_icon = "👑 " if member.role == UserRole.ADMIN else "👤 "
            
            buttons.append((
                f"{role_icon}{name_label}", 
                f"mem:view:{room_id}:{member.user_id}:{page}"
            ))
            adjust.append(1)

        if total_count > items_per_page:
            nav_buttons_count = 0
            
            if page > 0:
                buttons.append(("⬅️", f"mem:list:{room_id}:0:{page - 1}"))
                nav_buttons_count += 1
            
            total_pages = (total_count - 1) // items_per_page + 1
            buttons.append((f"{page + 1} / {total_pages}", "noop"))
            nav_buttons_count += 1

            if (page + 1) * items_per_page < total_count:
                buttons.append(("➡️", f"mem:list:{room_id}:0:{page + 1}"))
                nav_buttons_count += 1
            
            adjust.append(nav_buttons_count)

        buttons.append(("⬅️ Назад в меню", f"room_view:{room_id}"))
        adjust.append(1)

        return InlineKeyboardBuilderFactory.build_inline_keyboard(buttons, adjust=adjust)

    @staticmethod
    def member_settings_keyboard(room_id: int, user_id: int, role):
        buttons = []

        if role == UserRole.MEMBER:
            buttons.append((MemberInlineButtons.MAKE_ADMIN.value, f"member_settings:make_admin:{room_id}:{user_id}"))

        else:
            buttons.append((MemberInlineButtons.MAKE_MEMBER.value, f"member_settings:make_member:{room_id}:{user_id}"))

        buttons.append((MemberInlineButtons.KICK_MEMBER.value, f"member_settings:kick_member:{room_id}:{user_id}"))
        buttons.append((MemberInlineButtons.BACK_MEMBER, f"member_back:members:{room_id}:{user_id}"))

        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[1, 1, 1])

    @staticmethod
    def room_settings_keyboard(room_id: int):
        buttons = [
            (RoomSettingsButtons.RENAME_ROOM.value, f"room_admin_settings:rename:{room_id}"),
            (RoomSettingsButtons.DELETE_ROOM.value, f"room_admin_settings:delete:{room_id}"),
            (RoomSettingsButtons.BACK_ROOM.value, f"room:{room_id}")
        ]

        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[1, 1, 1])

    @staticmethod
    def create_acceptance_swap_keyboard(queue_id: int, pos_to: int):
        buttons = [
            (SwapInlineButtons.DECLINE.value, f"swap:decline:{queue_id}:{pos_to}"),
            (SwapInlineButtons.ACCEPT.value, f"swap:accept:{queue_id}:{pos_to}")
        ]

        return InlineKeyboardBuilderFactory().build_inline_keyboard(buttons, adjust=[2])
