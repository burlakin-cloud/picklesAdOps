import os
import logging
import requests
from .base import BaseChannel, empty_metrics, safe_div

log = logging.getLogger(__name__)

GADS_API_VERSION = "v23"


class GoogleAdsChannel(BaseChannel):
    name = "Google Ads"
    icon = "🔵"

    def is_configured(self, client: dict) -> bool:
        cfg = client.get("channels", {}).get("google_ads")
        if not cfg or not cfg.get("customer_id"):
            return False
        return all(os.environ.get(k) for k in [
            "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
            "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN",
        ])

    def _get_access_token(self) -> str:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     os.environ["GOOGLE_ADS_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
            "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
            "grant_type":    "refresh_token",
        }, timeout=15)
        r.raise_for_status()
        return r.json()["access_token"]

    def _headers(self, client: dict, access_token: str) -> tuple[dict, str]:
        cfg = client["channels"]["google_ads"]
        customer_id = cfg["customer_id"].replace("-", "").strip()
        login_cid   = cfg.get("login_customer_id", customer_id).replace("-", "").strip()
        return {
            "Authorization":    f"Bearer {access_token}",
            "developer-token":  os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
            "Content-Type":     "application/json",
            "login-customer-id": login_cid,
        }, customer_id

    def _search(self, client: dict, access_token: str, query: str) -> list:
        headers, cid = self._headers(client, access_token)
        url = f"https://googleads.googleapis.com/{GADS_API_VERSION}/customers/{cid}/googleAds:searchStream"
        r = requests.post(url, headers=headers, json={"query": query}, timeout=30)
        if not r.ok:
            log.error(f"Google Ads {r.status_code}: {r.text[:300]}")
            return []
        results = []
        for batch in r.json():
            results.extend(batch.get("results", []))
        return results

    def fetch_account(self, client: dict, date: str):
        try:
            token = self._get_access_token()
            rows = self._search(client, token, f"""
                SELECT metrics.cost_micros, metrics.clicks, metrics.impressions,
                       metrics.ctr, metrics.average_cpc, metrics.conversions,
                       metrics.cost_per_conversion
                FROM customer WHERE segments.date = '{date}'
            """)
            spend = clicks = impressions = conversions = 0.0
            for row in rows:
                m = row.get("metrics", {})
                spend       += int(m.get("costMicros", 0)) / 1_000_000
                clicks      += float(m.get("clicks", 0))
                impressions += float(m.get("impressions", 0))
                conversions += float(m.get("conversions", 0))
            return dict(
                spend=spend, clicks=clicks, impressions=impressions,
                ctr=safe_div(clicks, impressions) * 100,
                cpc=safe_div(spend, clicks),
                cpm=safe_div(spend, impressions) * 1000,
                leads=conversions,
                cpa=safe_div(spend, conversions),
            )
        except Exception as e:
            log.error(f"Google Ads account [{date}] {client['id']}: {e}")
            return empty_metrics()

    def fetch_campaigns(self, client: dict, date: str) -> list[dict]:
        try:
            token = self._get_access_token()
            rows = self._search(client, token, f"""
                SELECT campaign.id, campaign.name,
                       metrics.cost_micros, metrics.clicks, metrics.impressions,
                       metrics.ctr, metrics.average_cpc, metrics.conversions,
                       metrics.cost_per_conversion
                FROM campaign
                WHERE segments.date = '{date}'
                  AND campaign.status = 'ENABLED'
                  AND metrics.impressions > 0
                ORDER BY metrics.cost_micros DESC
            """)
            result = []
            for row in rows:
                m = row.get("metrics", {})
                c = row.get("campaign", {})
                spend = int(m.get("costMicros", 0)) / 1_000_000
                clicks = float(m.get("clicks", 0))
                impressions = float(m.get("impressions", 0))
                conversions = float(m.get("conversions", 0))
                result.append({
                    "id": str(c.get("id", "")),
                    "name": c.get("name", "Unknown"),
                    "spend": spend, "clicks": clicks, "impressions": impressions,
                    "ctr": safe_div(clicks, impressions) * 100,
                    "cpc": safe_div(spend, clicks),
                    "cpm": safe_div(spend, impressions) * 1000,
                    "leads": conversions,
                    "cpa": safe_div(spend, conversions),
                })
            return result
        except Exception as e:
            log.error(f"Google Ads campaigns [{date}] {client['id']}: {e}")
            return []
