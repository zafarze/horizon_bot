"""aiogram-middleware: подкладывает lang в data для каждого хендлера."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from .i18n import DEFAULT_LANG
from .lang import LangResolver


class LangMiddleware(BaseMiddleware):
    def __init__(self, resolver: LangResolver):
        self._resolver = resolver

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # aiogram сам выкладывает event_from_user в data — берём оттуда,
        # это работает для Message и CallbackQuery без if-веток.
        user = data.get("event_from_user")
        if user is not None:
            data["lang"] = await self._resolver.get(user.id)
        else:
            data["lang"] = DEFAULT_LANG
        return await handler(event, data)
