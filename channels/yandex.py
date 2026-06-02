import os
import logging
import requests
from .base import BaseChannel, empty_metrics, safe_div

log = logging.getLogger(__name__)


class YandexChannel(BaseChannel):
    name = "Яндекс"
    icon = "🟡"

    def is_configured(self, client: dict) -> bool:
        cfg = client.get("channels", {}).get("yandex")
        if not cfg or not cfg.get("client_login"):
            return False
        return bool(os.environ.get("YANDEX_TOKEN"))

    def _fetch(self, client: dict, date: str, field_names: list, report_type: str) -> list[list]:
        headers = {
            "Authorization":       f"Bearer {os.environ['YANDEX_TOKEN']}",
            "Client-Login":        client["channels"]["yandex"]["client_login"],
            "Accept-Language":     "ru",
            "processingMode":      "auto",
            "returnMoneyInMicros": "false",
            "skipReportHeader":    "true",
            "skipColumnHeader":    "false",
            "skipReportSummary":   "true",
        }
        body = {"params": {
            "SelectionCriteria": {"DateFrom": date, "DateTo": date},
            "FieldNames": field_names,
            "ReportName": f"{report_type}_{date}_{client['id']}",
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        }}
        r = requests.post("https://api.direct.yandex.com/json/v5/reports",
                          headers=headers, json=body, timeout=60)
        if r.status_code != 200:
            log.warning(f"Яндекс [{date}] {client['id']}: {r.status_code}")
            return []
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return []
        keys = lines[0].split("\t")
        return [dict(zip(keys, line.split("\t"))) for line in lines[1:]]

    def fetch_account(self, client: dict, date: str):
        try:
            rows = self._fetch(client, date,
                ["Cost","Clicks","Impressions","Ctr","AvgCpc","Conversions","CostPerConversion"],
                "ACCOUNT_PERFORMANCE_REPORT")
            if not rows:
                return empty_metrics()
            row = rows[0]
            spend = float(row.get("Cost", 0))
            clicks = float(row.get("Clicks", 0))
            impressions = float(row.get("Impressions", 0))
            leads = float(row.get("Conversions", 0))
            return dict(
                spend=spend, clicks=clicks, impressions=impressions,
                ctr=float(row.get("Ctr", 0)),
                cpc=float(row.get("AvgCpc", 0)),
                cpm=safe_div(spend, impressions) * 1000,
                leads=leads, cpa=safe_div(spend, leads),
            )
        except Exception as e:
            log.error(f"Яндекс account [{date}] {client['id']}: {e}")
            return empty_metrics()

    def fetch_campaigns(self, client: dict, date: str) -> list[dict]:
        try:
            rows = self._fetch(client, date,
                ["CampaignId","CampaignName","Cost","Clicks","Impressions",
                 "Ctr","AvgCpc","Conversions","CostPerConversion"],
                "CAMPAIGN_PERFORMANCE_REPORT")
            result = []
            for row in rows:
                spend = float(row.get("Cost", 0))
                clicks = float(row.get("Clicks", 0))
                impressions = float(row.get("Impressions", 0))
                leads = float(row.get("Conversions", 0))
                result.append({
                    "id": row.get("CampaignId", ""),
                    "name": row.get("CampaignName", "Unknown"),
                    "spend": spend, "clicks": clicks, "impressions": impressions,
                    "ctr": float(row.get("Ctr", 0)),
                    "cpc": float(row.get("AvgCpc", 0)),
                    "cpm": safe_div(spend, impressions) * 1000,
                    "leads": leads, "cpa": safe_div(spend, leads),
                })
            return sorted(result, key=lambda x: x["spend"], reverse=True)
        except Exception as e:
            log.error(f"Яндекс campaigns [{date}] {client['id']}: {e}")
            return []
