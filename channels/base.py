from abc import ABC, abstractmethod
from typing import Optional


# Стандартная структура метрик — одинакова для всех каналов
def empty_metrics() -> dict:
    return dict(spend=0.0, impressions=0, clicks=0,
                ctr=0.0, cpc=0.0, cpm=0.0, leads=0.0, cpa=0.0)


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


class BaseChannel(ABC):
    """
    Базовый класс для рекламного канала.
    Чтобы добавить новый канал — создай файл в channels/,
    унаследуйся от BaseChannel, реализуй три метода.
    """

    # Человекочитаемое название и иконка для UI
    name: str = "Unknown"
    icon: str = "📊"

    @abstractmethod
    def is_configured(self, client: dict) -> bool:
        """Проверяет, заданы ли все нужные токены для этого клиента."""
        pass

    @abstractmethod
    def fetch_account(self, client: dict, date: str) -> Optional[dict]:
        """
        Возвращает агрегированные метрики по всему аккаунту за дату.
        date: строка YYYY-MM-DD
        Возвращает dict с ключами: spend, impressions, clicks, ctr, cpc, cpm, leads, cpa
        Или None если ошибка.
        """
        pass

    @abstractmethod
    def fetch_campaigns(self, client: dict, date: str) -> list[dict]:
        """
        Возвращает список метрик по кампаниям.
        Каждый элемент: {"id": str, "name": str, **metrics}
        """
        pass
