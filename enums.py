from enum import Enum


MEMBERS_PER_PAGE = 10


class MainMenuButtons(Enum):
    CREATE_ROOM = "➕ Создать комнату"
    JOIN_ROOM = "🔑 Войти по коду"
    USER_ROOMS = "📂 Мои комнаты"
    CANCEL = "❌ Отмена"
    CONFIRM = "✅ Подтвердить"


class RoomInlineButtons(Enum):
    QUEUES_LIST = "📋 Список очередей"
    LEAVE_ROOM = "🚪 Покинуть комнату"
    BACK_ROOM = "⬅️ Назад"

    # Admin
    CREATE_QUEUE = "🆕 Создать очередь"
    SETTINGS_ROOM = "⚙️ Настройки комнаты"
    MEMBERS = "👥 Участники"


class QueueInlineButtons(Enum):
    JOIN_QUEUE = "📝 Записаться"
    EXIT_QUEUE = "🏃 Выйти из очереди"
    SWAP_QUEUE = "🤝 Поменяться"
    SKIP_QUEUE = "⏭ Пропустить вперед"
    BACK_QUEUE = "⬅️ Назад"

    # Admin
    SETTINGS_QUEUE = "🛠 Управление очередью"
    RENAME_QUEUE = "✏️ Переименовать"
    DELETE_QUEUE = "🗑 Удалить"
    CLEAR_QUEUE = "🧹 Очистить"
    MOVE_QUEUE = "↕️ Сдвинуть"


class MemberInlineButtons(Enum):
    MAKE_ADMIN = "👑 Сделать админом"
    MAKE_MEMBER = "👤 Разжаловать"
    KICK_MEMBER = "❌ Исключить из группы"
    BACK_MEMBER = "⬅️ Назад"


class RoomSettingsButtons(Enum):
    RENAME_ROOM = "🖋 Переименовать комнату"
    DELETE_ROOM = "🔥 Удалить комнату"
    BACK_ROOM = "⬅️ Назад"


class WritingCommentButtons(Enum):
    NO_COMMENT = "⏭ Без комментария"


class SwapInlineButtons(Enum):
    ACCEPT = "✅ Принять"
    DECLINE = "❌ Отклонить"
    CANCEL = "🗑 Отменить запрос"