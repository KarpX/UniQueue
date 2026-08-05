from enum import Enum


class MainMenuButtons(Enum):
    CREATE_ROOM = "Создать комнату"
    JOIN_ROOM = "Присоединиться к комнате"
    USER_ROOMS = "Мои комнаты"
    CANCEL = "Отмена"


class QueueInlineButtons(Enum):
    JOIN_QUEUE = "Записаться"
    EXIT_QUEUE = "Выйти"
    SKIP_QUEUE = "Пропустить"
    BACK_QUEUE = "Назад"