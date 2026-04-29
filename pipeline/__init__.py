"""Pipeline-пакет. Dispatcher импортируется явно из pipeline.dispatcher."""
from .importance import ImportanceRules, classify

__all__ = ["classify", "ImportanceRules"]
