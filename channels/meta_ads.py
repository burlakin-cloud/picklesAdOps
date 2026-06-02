import os
import json
import logging
import requests
from .base import BaseChannel, empty_metrics, safe_div

log = logging.getLogger(__name__)


class MetaAdsChannel(BaseChannel):
    name = "Meta Ads"
    icon = "🟣"

    def is_configured(self, client: dict) -> bool:
        cfg = client.get("channels", {}).get("meta_ads")
        if not cfg or not cfg.get("ad_account_id"):
            return False
        return bool(os.environ.get("META_ACCESS_TOKEN"))

    def fetch_account(self, client: dict, date: str):
        try:
            account_id = client["channels"]["meta_ads"]["ad_account_id"]
            url = f"https://graph.facebook.com/v19.0/{account_id}/insights"
            r = requests.get(url, params={
                "access_token": os.environ["META_ACCESS_TOKEN"],
                "time_range":   json.dumps({"since": date, "until": date}),
                "fields":       "spend,clicks,impressions,ctr,cpc,actions",
                "level":        "account",
            }, timeout=30)
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                return empty_metrics()
            d = data[0]
            spend       = float(d.get("spend", 0))
            clicks      = float(d.get("clicks", 0))
            impressions = float(d.get("impressions", 0))
            ctr         = float(d.get("ctr", 0))
            cpc         = float(d.get("cpc", 0))
            leads       = sum(float(a.get("value", 0)) for a in d.get("actions", [])
                              if a["action_type"] in ("lead", "offsite_conversion.fb_pixel_lead"))
            return dict(
                spend=spend, clicks=clicks, impressions=impressions,
                ctr=ctr, cpc=cpc,
                cpm=safe_div(spend, impressions) * 1000,
                leads=leads, cpa=safe_div(spend, leads),
            )
        except Exception as e:
            log.error(f"Meta account [{date}] {client['id']}: {e}")
            return empty_metrics()

    def fetch_campaigns(self, client: dict, date: str) -> list[dict]:
        try:
            account_id = client["channels"]["meta_ads"]["ad_account_id"]
            url = f"https://graph.facebook.com/v19.0/{account_id}/insights"
            r = requests.get(url, params={
                "access_token": os.environ["META_ACCESS_TOKEN"],
                "time_range":   json.dumps({"since": date, "until": date}),
                "fields":       "campaign_id,campaign_name,spend,clicks,impressions,ctr,cpc,actions",
                "level":        "campaign",
            }, timeout=30)
            r.raise_for_status()
            result = []
            for d in r.json().get("data", []):
                spend       = float(d.get("spend", 0))
                clicks      = float(d.get("clicks", 0))
                impressions = float(d.get("impressions", 0))
                leads       = sum(float(a.get("value", 0)) for a in d.get("actions", [])
                                  if a["action_type"] in ("lead", "offsite_conversion.fb_pixel_lead"))
                result.append({
                    "id": d.get("campaign_id", ""),
                    "name": d.get("campaign_name", "Unknown"),
                    "spend": spend, "clicks": clicks, "impressions": impressions,
                    "ctr": float(d.get("ctr", 0)),
                    "cpc": float(d.get("cpc", 0)),
                    "cpm": safe_div(spend, impressions) * 1000,
                    "leads": leads, "cpa": safe_div(spend, leads),
                })
            return sorted(result, key=lambda x: x["spend"], reverse=True)
        except Exception as e:
            log.error(f"Meta campaigns [{date}] {client['id']}: {e}")
            return []
