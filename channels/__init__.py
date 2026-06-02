from .google_ads import GoogleAdsChannel
from .meta_ads import MetaAdsChannel
from .yandex import YandexChannel
from .tiktok import TikTokAdsChannel

# Реестр всех каналов — добавить новый канал = одна строка здесь
ALL_CHANNELS = {
    "google_ads": GoogleAdsChannel(),
    "meta_ads":   MetaAdsChannel(),
    "yandex":     YandexChannel(),
    "tiktok":     TikTokAdsChannel(),
}
