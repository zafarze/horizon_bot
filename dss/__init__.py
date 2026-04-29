"""DSS-пакет. Импорты — на месте использования, чтобы models был доступен без aiohttp."""
from .models import Event

__all__ = ["Event"]
