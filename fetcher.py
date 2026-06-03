"""
fetcher.py — ручная отправка отчёта в Telegram.
Запуск: python fetcher.py [--telegram-only] [--no-telegram]
"""
import sys
import json
import logging
import os
import requests
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

NO_TELEGRAM   = "--no-telegram"   in sys.argv
TELEGRAM_ONLY = "--telegram-only" in sys.argv

from channels import ALL_CHANNELS
import db

TODAY      = date.today()
YESTERDAY  = (TODAY - timedelta(days=1)).isoformat()
DAY_BEFORE = (TODAY - timedelta(days=2)).isoformat()

# ── Форматирование ────────────────────────────────────────────────────────
def fmt_money(amount: float, currency: str) -> str:
    symbols = {"USD": "$", "EUR": "€", "RUB": "₽", "UZS": "сум",
               "KZT": "₸", "GBP": "£", "UAH": "₴", "TRY": "₺"}
    sym = symbols.get(currency, currency + " ")
    if currency in ("UZS", "KZT", "RUB"):
        return f"{amount:,.0f} {sym}"
    return f"{sym}{amount:,.2f}"

def pct_change(new: float, old: float):
    return None if old == 0 else (new - old) / old

def fmt_delta(delta) -> str:
    if delta is None:
        return "—"
    return f"{'+'if delta>=0 else ''}{delta*100:.1f}%"

def flag(metric: str, delta) -> str:
    if delta is None:
        return ""
    bad = (delta < 0) if metric in ("ctr", "leads", "clicks") else (delta > 0)
    if not bad:
        return ""
    return " 🔴" if abs(delta) >= 0.30 else (" ⚠️" if abs(delta) >= 0.20 else "")

def has_flags(y: dict, p: dict) -> bool:
    return any(flag(m, pct_change(y[m], p[m])) for m in ("spend", "ctr", "cpc", "leads"))

def send_telegram(chat_id: str, text: str):
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        log.warning("TG_BOT_TOKEN не задан")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()
        log.info("Telegram отправлен ✓")
    except Exception as e:
        log.error(f"Telegram error: {e}")

def get_channel_currency(client: dict, key: str) -> str:
    return client.get("channels", {}).get(key, {}).get("currency", "USD")

def get_summary_currency(client: dict) -> str:
    return client.get("summary_currency", "USD")

def process_client(client: dict):
    cid = client["id"]
    log.info(f"== {client['name']} ==")

    # Загружаем данные если не --telegram-only
    if not TELEGRAM_ONLY:
        for key, channel in ALL_CHANNELS.items():
            if not channel.is_configured(client):
                continue
            log.info(f"  {channel.name}...")
            for d in [YESTERDAY, DAY_BEFORE]:
                acc = channel.fetch_account(client, d)
                if acc:
                    db.upsert_account(cid, key, d, acc)
                camps = channel.fetch_campaigns(client, d)
                if camps:
                    db.upsert_campaigns(cid, key, d, camps)

    if NO_TELEGRAM:
        log.info("  Telegram пропущен")
        return

    today_data = db.get_account_metrics(cid, YESTERDAY)
    yest_data  = db.get_account_metrics(cid, DAY_BEFORE)
    yest_by_ch = {r["channel"]: r for r in yest_data}
    empty      = {"spend": 0, "clicks": 0, "impressions": 0, "ctr": 0,
                  "cpc": 0, "cpm": 0, "leads": 0, "cpa": 0}
    summary_cur = get_summary_currency(client)

    if not today_data:
        return

    # Итого по всем каналам
    t     = {"spend": 0.0, "clicks": 0.0, "impressions": 0.0, "leads": 0.0}
    p_sum = {"spend": 0.0, "clicks": 0.0, "impressions": 0.0, "leads": 0.0}
    for row in today_data:
        t["spend"]       += row["spend"]
        t["clicks"]      += row["clicks"]
        t["impressions"] += row["impressions"]
        t["leads"]       += row["leads"]
    for row in yest_data:
        p_sum["spend"]       += row["spend"]
        p_sum["clicks"]      += row["clicks"]
        p_sum["impressions"] += row["impressions"]
        p_sum["leads"]       += row["leads"]

    t_cpa = t["spend"] / t["leads"]       if t["leads"]       else 0
    t_cpc = t["spend"] / t["clicks"]      if t["clicks"]      else 0
    t_cpm = t["spend"] / t["impressions"] * 1000 if t["impressions"] else 0
    t_ctr = t["clicks"] / t["impressions"] * 100 if t["impressions"] else 0

    d_spend  = pct_change(t["spend"],  p_sum["spend"])
    d_leads  = pct_change(t["leads"],  p_sum["leads"])
    d_clicks = pct_change(t["clicks"], p_sum["clicks"])

    sym = summary_cur
    total_block = (
        f"📈 *Итого по всем каналам* ({sym})\n"
        f"├ Расход:     `{fmt_money(t['spend'], sym)}` ({fmt_delta(d_spend)}){flag('spend', d_spend)}\n"
        f"├ Конверсии:  `{t['leads']:.0f}` ({fmt_delta(d_leads)}){flag('leads', d_leads)}\n"
        f"├ CPA:        `{fmt_money(t_cpa, sym)}`\n"
        f"├ Клики:      `{int(t['clicks']):,}` ({fmt_delta(d_clicks)}){flag('clicks', d_clicks)}\n"
        f"├ Показы:     `{int(t['impressions']):,}`\n"
        f"├ CPC:        `{fmt_money(t_cpc, sym)}`\n"
        f"├ CPM:        `{fmt_money(t_cpm, sym)}`\n"
        f"└ CTR:        `{t_ctr:.2f}%`"
    )

    # Разбивка по каналам в нативной валюте
    blocks = []
    for row in today_data:
        key  = row["channel"]
        chan = ALL_CHANNELS.get(key)
        if not chan:
            continue
        cur    = get_channel_currency(client, key)
        p      = yest_by_ch.get(key, empty.copy())
        status = "🔴" if has_flags(row, p) else "🟢"

        def money(val: float) -> str:
            return f"`{fmt_money(val, cur)}`"

        d_s  = pct_change(row["spend"],  p["spend"])
        d_l  = pct_change(row["leads"],  p["leads"])
        d_cl = pct_change(row["clicks"], p["clicks"])
        d_c  = pct_change(row["ctr"],    p["ctr"])
        d_k  = pct_change(row["cpc"],    p["cpc"])

        block = (
            f"{status} *{chan.icon} {chan.name}* ({cur})\n"
            f"├ Расход:     {money(row['spend'])} ({fmt_delta(d_s)}){flag('spend', d_s)}\n"
            f"├ Конверсии:  `{row['leads']:.0f}` ({fmt_delta(d_l)}){flag('leads', d_l)}\n"
            f"├ CPA:        {money(row['cpa'])}\n"
            f"├ Клики:      `{int(row['clicks']):,}` ({fmt_delta(d_cl)}){flag('clicks', d_cl)}\n"
            f"├ Показы:     `{int(row['impressions']):,}`\n"
            f"├ CPC:        {money(row['cpc'])} ({fmt_delta(d_k)}){flag('cpc', d_k)}\n"
            f"├ CPM:        {money(row['cpm'])}\n"
            f"└ CTR:        `{row['ctr']:.2f}%` ({fmt_delta(d_c)}){flag('ctr', d_c)}"
        )
        blocks.append(block)

    if blocks:
        msg = (
            f"📊 *{client['name']}*\n"
            f"📅 {YESTERDAY} vs {DAY_BEFORE}\n"
            f"{'─'*30}\n\n"
            f"{total_block}\n\n"
            f"{'─'*30}\n\n"
            + "\n\n".join(blocks)
            + f"\n\n{'─'*30}\n_🔴 критично | ⚠️ внимание_"
        )
        send_telegram(client["telegram_chat_id"], msg)


def main():
    db.init_db()
    with open("clients.json") as f:
        clients = json.load(f)
    for client in clients:
        try:
            process_client(client)
        except Exception as e:
            log.error(f"Ошибка {client['id']}: {e}")


if __name__ == "__main__":
    main()
