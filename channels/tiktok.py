import os
import logging
from .base import BaseChannel, empty_metrics

log = logging.getLogger(__name__)


class TikTokAdsChannel(BaseChannel):
    """
    Заготовка для TikTok Ads API.
    Когда будешь подключать — реализуй fetch_account и fetch_campaigns.
    Документация: https://ads.tiktok.com/marketing_api/docs
    """
    name = "TikTok Ads"
    icon = "⚫"

    def is_configured(self, client: dict) -> bool:
        cfg = client.get("channels", {}).get("tiktok")
        if not cfg or not cfg.get("advertiser_id"):
            return False
        return bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))

    def fetch_account(self, client: dict, date: str):
        log.info(f"TikTok Ads: интеграция в разработке")
        return empty_metrics()

    def fetch_campaigns(self, client: dict, date: str) -> list[dict]:
        return []
