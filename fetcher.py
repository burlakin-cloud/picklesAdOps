"""
fetcher.py — сбор данных и отправка дайджеста в Telegram.
Запускается ежедневно через GitHub Actions или cron.
"""
import json
import logging
import os
import requests
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

from channels import ALL_CHANNELS
import db

# ══════════════════════════════════════════════════════════════════════════════
# ПОРОГИ АНОМАЛИЙ
# ══════════════════════════════════════════════════════════════════════════════
THRESHOLDS = {
    "spend":  {"warn": 0.20, "crit": 0.30},
    "ctr":    {"warn": 0.15, "crit": 0.20},
    "cpc":    {"warn": 0.20, "crit": 0.25},
    "leads":  {"warn": 0.15, "crit": 0.20},
}

TODAY     = date.today()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
DAY_BEFORE = (TODAY - timedelta(days=2)).isoformat()


def pct_change(new, old):
    return None if old == 0 else (new - old) / old


def flag(metric, delta):
    if delta is None:
        return ""
    t = THRESHOLDS.get(metric, {})
    bad = (delta < 0) if metric in ("ctr", "leads") else (delta > 0)
    if not bad:
        return ""
    if abs(delta) >= t.get("crit", 999):
        return " 🔴"
    if abs(delta) >= t.get("warn", 999):
        return " ⚠️"
    return ""


def fmt_delta(delta):
    if delta is None:
        return "—"
    return f"{'+'if delta>=0 else ''}{delta*100:.1f}%"


def metrics_lines(y, p):
    d_spend = pct_change(y["spend"], p["spend"])
    d_ctr   = pct_change(y["ctr"],   p["ctr"])
    d_cpc   = pct_change(y["cpc"],   p["cpc"])
    d_leads = pct_change(y["leads"], p["leads"])
    return (
        f"├ Расход:  `{y['spend']:,.2f} $` ({fmt_delta(d_spend)}){flag('spend', d_spend)}\n"
        f"├ CTR:     `{y['ctr']:.2f}%` ({fmt_delta(d_ctr)}){flag('ctr', d_ctr)}\n"
        f"├ CPC:     `{y['cpc']:,.2f} $` ({fmt_delta(d_cpc)}){flag('cpc', d_cpc)}\n"
        f"├ CPM:     `{y['cpm']:,.2f} $`\n"
        f"├ Лиды:   `{y['leads']:.0f}` ({fmt_delta(d_leads)}){flag('leads', d_leads)}\n"
        f"└ CPA:     `{y['cpa']:,.2f} $`\n"
    )


def has_flags(y, p):
    for m in ("spend", "ctr", "cpc", "leads"):
        if flag(m, pct_change(y[m], p[m])):
            return True
    return False


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
            timeout=15
        )
        r.raise_for_status()
    except Exception as e:
        log.error(f"Telegram error: {e}")


def process_client(client: dict):
    cid = client["id"]
    log.info(f"== {client['name']} ==")

    # Собираем данные по каждому каналу
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

    # Строим и отправляем Telegram
    today_data    = db.get_account_metrics(cid, YESTERDAY)
    yesterday_data = db.get_account_metrics(cid, DAY_BEFORE)

    yest_by_ch = {r["channel"]: r for r in yesterday_data}
    blocks = []
    for row in today_data:
        ch   = row["channel"]
        chan = ALL_CHANNELS.get(ch)
        if not chan:
            continue
        p = yest_by_ch.get(ch, {k: 0.0 for k in row})
        status = "🔴" if has_flags(row, p) else "🟢"
        blocks.append(f"{status} *{chan.icon} {chan.name}*\n{metrics_lines(row, p)}")

    if not blocks:
        return

    # Сообщение 1 — сводка
    msg1 = (
        f"📊 *{client['name']}*\n"
        f"📅 {YESTERDAY} vs {DAY_BEFORE}\n"
        f"{'─'*30}\n\n" + "\n".join(blocks) +
        f"\n{'─'*30}\n_🔴 критично | ⚠️ внимание_"
    )
    send_telegram(client["telegram_chat_id"], msg1)

    # Сообщение 2 — кампании (только каналы с флагами)
    camp_blocks = []
    for ch, chan in ALL_CHANNELS.items():
        if not chan.is_configured(client):
            continue
        camps_today = db.get_campaign_metrics(cid, ch, YESTERDAY)
        camps_yest  = {c["id"]: c for c in db.get_campaign_metrics(cid, ch, DAY_BEFORE)}
        if not camps_today:
            continue
        camp_blocks.append(f"*{chan.icon} {chan.name}*")
        for c in camps_today:
            p = camps_yest.get(c["id"], {k: 0.0 for k in c})
            st = "🔴" if has_flags(c, p) else "🟢"
            camp_blocks.append(f"{st} *{c['name']}*\n{metrics_lines(c, p)}")

    if camp_blocks:
        msg2 = (
            f"📋 *Кампании — {client['name']}*\n"
            f"📅 {YESTERDAY} vs {DAY_BEFORE}\n"
            f"{'─'*30}\n\n" + "\n".join(camp_blocks)
        )
        send_telegram(client["telegram_chat_id"], msg2)


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
