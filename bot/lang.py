"""In-memory cache поверх db.user_lang. Чтение частое, запись редкая."""
from __future__ import annotations

import asyncio

from db import DB

from .i18n import DEFAULT_LANG, normalize_lang


class LangResolver:
    def __init__(self, db: DB, default: str = DEFAULT_LANG):
        self._db = db
        self._default = default
        self._cache: dict[int, str] = {}
        self._lock = asyncio.Lock()

    async def get(self, user_id: int) -> str:
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        # Двойная проверка под локом — защита от стампеда из middleware.
        async with self._lock:
            cached = self._cache.get(user_id)
            if cached is not None:
                return cached
            lang = await self._db.get_lang(user_id, self._default)
            self._cache[user_id] = lang
            return lang

    async def set(self, user_id: int, lang: str) -> None:
        lang = normalize_lang(lang)
        await self._db.set_lang(user_id, lang)
        self._cache[user_id] = lang

    def invalidate(self, user_id: int) -> None:
        self._cache.pop(user_id, None)
