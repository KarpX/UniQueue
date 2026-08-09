from aiogram.fsm.state import StatesGroup, State


class CreateRoomState(StatesGroup):
    waiting_for_room_name = State()
    waiting_for_room_rename = State()


class JoinRoomState(StatesGroup):
    waiting_for_invite_code = State()


class CreateQueueState(StatesGroup):
    waiting_for_queue_name = State()
    waiting_for_queue_rename = State()


class SwapEntriesState(StatesGroup):
    waiting_postition = State()
    waiting_comment = State()