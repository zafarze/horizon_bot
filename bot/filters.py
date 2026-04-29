"""Фильтры aiogram."""
from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message


class AdminFilter(Filter):
    def __init__(self, admin_ids: list[int]):
        self.admin_ids = set(admin_ids)

    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in self.admin_ids
