"""FSM states of the admin panel."""

from aiogram.fsm.state import State, StatesGroup


class Admin(StatesGroup):
    waiting = State()
    confirm = State()
