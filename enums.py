from enum import Enum


MEMBERS_PER_PAGE = 10

class MainMenuButtons(Enum):
    CREATE_ROOM = "Создать комнату"
    JOIN_ROOM = "Присоединиться к комнате"
    USER_ROOMS = "Мои комнаты"
    CANCEL = "Отмена"


class RoomInlineButtons(Enum):
    QUEUES_LIST = "Очереди"
    LEAVE_ROOM = "Выйти из комнаты"
    BACK_ROOM = "Назад"

    # Admin
    CREATE_QUEUE = "Создать очередь"
    SETTINGS_ROOM = "Настройки"
    MEMBERS = "Участники"


class QueueInlineButtons(Enum):
    JOIN_QUEUE = "Записаться"
    EXIT_QUEUE = "Выйти"
    SKIP_QUEUE = "Пропустить"
    BACK_QUEUE = "Назад"

    # Admin
    SETTINGS_QUEUE = "Настройки"
    RENAME_QUEUE = "Переименовать"
    DELETE_QUEUE = "Удалить"

    