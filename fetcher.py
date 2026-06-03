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

THRESHOLDS = {
    "spend":  {"warn": 0.20, "crit": 0.30},
    "ctr":    {"warn": 0.15, "crit": 0.20},
    "cpc":    {"warn": 0.20, "crit": 0.25},
    "leads":  {"warn": 0.15, "crit": 0.20},
}

TODAY      = date.today()
YESTERDAY  = (TODAY - timedelta(days=1)).isoformat()
DAY_BEFORE = (TODAY - timedelta(days=2)).isoformat()


def pct_change(new, old):
    return None if old == 0 else (new - old) / old

def flag(metric, delta):
    if delta is None:
        return ""
    bad = (delta < 0) if metric in ("ctr", "leads") else (delta > 0)
    if not bad:
        return ""
    if abs(delta) >= 0.30:
        return " 🔴"
    if abs(delta) >= 0.20:
        return " ⚠️"
    return ""

def fmt_delta(delta):
    if delta is None:
        return "—"
    return f"{'+'if delta>=0 else ''}{delta*100:.1f}%"

def has_flags(y, p):
    return any(flag(m, pct_change(y[m], p[m])) for m in ("spend","ctr","cpc","leads"))

def send_telegram(chat_id, text):
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        log.warning("TG_BOT_TOKEN не задан")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15
        )
        r.raise_for_status()
        log.info("Telegram отправлен ✓")
    except Exception as e:
        log.error(f"Telegram error: {e}")

def process_client(client):
    cid = client["id"]
    log.info(f"== {client['name']} ==")

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

    today_data    = db.get_account_metrics(cid, YESTERDAY)
    yest_data     = db.get_account_metrics(cid, DAY_BEFORE)
    yest_by_ch    = {r["channel"]: r for r in yest_data}
    empty         = {"spend":0,"clicks":0,"impressions":0,"ctr":0,"cpc":0,"cpm":0,"leads":0,"cpa":0}

    if not today_data:
        return

    # Итоговый блок по всем каналам
    t = {"spend":0,"clicks":0,"impressions":0,"leads":0}
    p_sum = {"spend":0,"clicks":0,"impressions":0,"leads":0}
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

    t_cpa = t["spend"]/t["leads"] if t["leads"] else 0
    t_cpc = t["spend"]/t["clicks"] if t["clicks"] else 0
    t_cpm = t["spend"]/t["impressions"]*1000 if t["impressions"] else 0
    t_ctr = t["clicks"]/t["impressions"]*100 if t["impressions"] else 0

    d_spend = pct_change(t["spend"],  p_sum["spend"])
    d_leads = pct_change(t["leads"],  p_sum["leads"])
    d_clicks= pct_change(t["clicks"], p_sum["clicks"])

    total_block = (
        f"📈 *Итого по всем каналам*\n"
        f"├ Расход:    `${t['spend']:,.2f}` ({fmt_delta(d_spend)}){flag('spend',d_spend)}\n"
        f"├ Конверсии: `{t['leads']:.0f}` ({fmt_delta(d_leads)}){flag('leads',d_leads)}\n"
        f"├ CPA:       `${t_cpa:,.2f}`\n"
        f"├ Клики:     `{int(t['clicks']):,}` ({fmt_delta(d_clicks)})\n"
        f"├ Показы:    `{int(t['impressions']):,}`\n"
        f"├ CPC:       `${t_cpc:,.2f}`\n"
        f"├ CPM:       `${t_cpm:,.2f}`\n"
        f"└ CTR:       `{t_ctr:.2f}%`"
    )

    # Блоки по каналам
    blocks = []
    for row in today_data:
        ch   = row["channel"]
        chan = ALL_CHANNELS.get(ch)
        if not chan:
            continue
        p      = yest_by_ch.get(ch, empty.copy())
        status = "🔴" if has_flags(row, p) else "🟢"
        d_s = pct_change(row["spend"], p["spend"])
        d_c = pct_change(row["ctr"],   p["ctr"])
        d_k = pct_change(row["cpc"],   p["cpc"])
        d_l = pct_change(row["leads"], p["leads"])
        d_cl= pct_change(row["clicks"],p["clicks"])
        block = (
            f"{status} *{chan.icon} {chan.name}*\n"
            f"├ Расход:    `${row['spend']:,.2f}` ({fmt_delta(d_s)}){flag('spend',d_s)}\n"
            f"├ Конверсии: `{row['leads']:.0f}` ({fmt_delta(d_l)}){flag('leads',d_l)}\n"
            f"├ CPA:       `${row['cpa']:,.2f}`\n"
            f"├ Клики:     `{int(row['clicks']):,}` ({fmt_delta(d_cl)})\n"
            f"├ Показы:    `{int(row['impressions']):,}`\n"
            f"├ CPC:       `${row['cpc']:,.2f}` ({fmt_delta(d_k)}){flag('cpc',d_k)}\n"
            f"├ CPM:       `${row['cpm']:,.2f}`\n"
            f"└ CTR:       `{row['ctr']:.2f}%` ({fmt_delta(d_c)}){flag('ctr',d_c)}"
        )
        blocks.append(block)

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
