"""
app.py — Flask веб-приложение + REST API + отправка Telegram.
"""
import json, os, subprocess, threading, uuid, time
import requests as req_lib
from datetime import date, timedelta, datetime
from flask import Flask, jsonify, render_template, request, abort
from dotenv import load_dotenv

load_dotenv()
from channels import ALL_CHANNELS
import db

app = Flask(__name__)
db.init_db()

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me")
PYTHON      = "/home/pickles_ads/venv/bin/python"
WORKDIR     = "/home/pickles_ads"

# ── Курсы валют (кэш 1 час) ───────────────────────────────────────────────
_rates_cache: dict = {"rates": {}, "ts": 0.0}

def get_rates() -> dict:
    if time.time() - _rates_cache["ts"] > 3600:
        try:
            r = req_lib.get("https://open.er-api.com/v6/latest/USD", timeout=10)
            if r.ok:
                _rates_cache["rates"] = r.json().get("rates", {})
                _rates_cache["ts"] = time.time()
        except Exception as e:
            print(f"Exchange rate error: {e}")
    return _rates_cache["rates"]

def convert_to_usd(amount: float, currency: str) -> float:
    if not currency or currency == "USD":
        return amount
    rate = get_rates().get(currency)
    return amount / rate if rate else amount

def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0

# ── File-based job status (shared across workers) ─────────────────────────
def _jp(jid: str) -> str:
    return f"/tmp/pads_{jid}.json"

def job_set(jid: str, status: str, done: int = 0, total: int = 0):
    with open(_jp(jid), "w") as f:
        json.dump({"status": status, "done": done, "total": total}, f)

def job_inc(jid: str, total: int):
    try:
        with open(_jp(jid)) as f:
            d = json.load(f)
        d["done"] = d.get("done", 0) + 1
        with open(_jp(jid), "w") as f:
            json.dump(d, f)
    except:
        pass

def job_get(jid: str) -> dict:
    try:
        with open(_jp(jid)) as f:
            return json.load(f)
    except:
        return {"status": "not_found"}

# ── Клиенты ───────────────────────────────────────────────────────────────
def load_clients() -> list:
    with open("clients.json") as f:
        return json.load(f)

def get_client_by_id(client_id: str) -> dict | None:
    return next((c for c in load_clients() if c["id"] == client_id), None)

def get_channel_currency(client: dict, channel_key: str) -> str:
    return client.get("channels", {}).get(channel_key, {}).get("currency", "USD")

def get_summary_currency(client: dict) -> str:
    return client.get("summary_currency", "USD")

def all_dates(date_from: str, date_to: str) -> list[str]:
    d0 = datetime.fromisoformat(date_from).date()
    d1 = datetime.fromisoformat(date_to).date()
    days = []
    while d0 <= d1:
        days.append(d0.isoformat())
        d0 += timedelta(days=1)
    return days

# ── Форматирование ────────────────────────────────────────────────────────
def pct_change(new: float, old: float) -> float | None:
    return None if old == 0 else (new - old) / old

def flag(metric: str, delta: float | None) -> str:
    if delta is None:
        return ""
    bad = (delta < 0) if metric in ("ctr", "leads", "clicks") else (delta > 0)
    if not bad:
        return ""
    return " 🔴" if abs(delta) >= 0.30 else (" ⚠️" if abs(delta) >= 0.20 else "")

def fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    return f"{'+'if delta>=0 else ''}{delta*100:.1f}%"

def fmt_money(amount: float, currency: str) -> str:
    symbols = {"USD": "$", "EUR": "€", "RUB": "₽", "UZS": "сум",
               "KZT": "₸", "GBP": "£", "UAH": "₴", "TRY": "₺"}
    sym = symbols.get(currency, currency + " ")
    if currency in ("UZS", "KZT", "RUB"):
        return f"{amount:,.0f} {sym}"
    return f"{sym}{amount:,.2f}"

def has_flags(y: dict, p: dict) -> bool:
    return any(flag(m, pct_change(y[m], p[m])) for m in ("spend", "ctr", "cpc", "leads"))

# ── Telegram ──────────────────────────────────────────────────────────────
def tg_send(chat_id: str, text: str):
    token = os.environ.get("TG_BOT_TOKEN", "")
    if not token:
        return
    try:
        req_lib.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception as e:
        print(f"TG error: {e}")

def metrics_lines(y: dict, p: dict, currency: str, summary_cur: str = "USD") -> str:
    """Форматирует блок метрик для Telegram в нужном порядке."""
    def money(val: float) -> str:
        s = fmt_money(val, currency)
        if currency != summary_cur and val > 0:
            s += f" (≈{fmt_money(convert_to_usd(val, currency), summary_cur)})"
        return f"`{s}`"

    rows = [
        ("├", "Расход",     money(y["spend"]),                pct_change(y["spend"],       p["spend"]),  "spend"),
        ("├", "Конверсии",  f"`{y['leads']:.0f}`",            pct_change(y["leads"],       p["leads"]),  "leads"),
        ("├", "CPA",        money(y["cpa"]),                   None,                                     "cpa"),
        ("├", "Клики",      f"`{int(y['clicks']):,}`",         pct_change(y["clicks"],      p["clicks"]), "clicks"),
        ("├", "Показы",     f"`{int(y['impressions']):,}`",    None,                                     "impr"),
        ("├", "CPC",        money(y["cpc"]),                   pct_change(y["cpc"],         p["cpc"]),   "cpc"),
        ("├", "CPM",        money(y["cpm"]),                   None,                                     "cpm"),
        ("└", "CTR",        f"`{y['ctr']:.2f}%`",              pct_change(y["ctr"],         p["ctr"]),   "ctr"),
    ]
    return "\n".join(
        f"{sym} {lbl}: {val} ({fmt_delta(d)}){flag(m, d)}"
        for sym, lbl, val, d, m in rows
    ) + "\n"

def build_and_send(client: dict, date_from: str, date_to: str,
                   compare_from: str, compare_to: str, sections: list):
    cid         = client["id"]
    summary_cur = get_summary_currency(client)
    empty       = {"spend": 0, "impressions": 0, "clicks": 0,
                   "ctr": 0, "cpc": 0, "cpm": 0, "leads": 0, "cpa": 0}
    label  = date_from if date_from == date_to else f"{date_from} — {date_to}"
    clabel = (compare_from if compare_from == compare_to
              else f"{compare_from} — {compare_to}") if compare_from else ""

    if "summary" in sections:
        today_rows   = {r["channel"]: r for r in db.get_account_metrics_range(cid, date_from, date_to)}
        compare_rows = ({r["channel"]: r for r in db.get_account_metrics_range(cid, compare_from, compare_to)}
                        if compare_from else {})

        # Итого по всем каналам в summary_currency
        t     = {"spend": 0.0, "clicks": 0.0, "impressions": 0.0, "leads": 0.0}
        p_sum = {"spend": 0.0, "clicks": 0.0, "impressions": 0.0, "leads": 0.0}
        for key in ALL_CHANNELS:
            if key not in today_rows:
                continue
            cur = get_channel_currency(client, key)
            y   = today_rows[key]
            p   = compare_rows.get(key, empty.copy())
            t["spend"]       += convert_to_usd(y["spend"], cur)
            t["clicks"]      += y["clicks"]
            t["impressions"] += y["impressions"]
            t["leads"]       += y["leads"]
            p_sum["spend"]       += convert_to_usd(p["spend"], cur)
            p_sum["clicks"]      += p["clicks"]
            p_sum["impressions"] += p["impressions"]
            p_sum["leads"]       += p["leads"]

        t_cpa = safe_div(t["spend"], t["leads"])
        t_cpc = safe_div(t["spend"], t["clicks"])
        t_cpm = safe_div(t["spend"], t["impressions"]) * 1000
        t_ctr = safe_div(t["clicks"], t["impressions"]) * 100
        sym   = summary_cur

        d_spend  = pct_change(t["spend"],  p_sum["spend"])
        d_leads  = pct_change(t["leads"],  p_sum["leads"])
        d_clicks = pct_change(t["clicks"], p_sum["clicks"])

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

        # Разбивка по каналам
        blocks = []
        for key, chan in ALL_CHANNELS.items():
            if key not in today_rows:
                continue
            cur    = get_channel_currency(client, key)
            y      = today_rows[key]
            p      = compare_rows.get(key, empty.copy())
            status = "🔴" if has_flags(y, p) else "🟢"
            blocks.append(
                f"{status} *{chan.icon} {chan.name}* ({cur})\n"
                f"{metrics_lines(y, p, cur, summary_cur)}"
            )

        if blocks:
            cmp_str = f" vs {clabel}" if clabel else ""
            msg = (
                f"📊 *{client['name']}*\n📅 {label}{cmp_str}\n{'─'*30}\n\n"
                f"{total_block}\n\n{'─'*30}\n\n"
                + "\n".join(blocks)
                + f"\n{'─'*30}\n_🔴 критично | ⚠️ внимание_"
            )
            tg_send(client["telegram_chat_id"], msg)

    # Разбивка по кампаниям
    for s in sections:
        if not s.endswith("_campaigns"):
            continue
        key  = s.replace("_campaigns", "")
        chan = ALL_CHANNELS.get(key)
        if not chan:
            continue
        cur           = get_channel_currency(client, key)
        today_camps   = db.get_campaign_metrics_range(cid, key, date_from, date_to)
        compare_camps = ({c["id"]: c for c in db.get_campaign_metrics_range(cid, key, compare_from, compare_to)}
                         if compare_from else {})
        if not today_camps:
            continue
        lines = [f"*{chan.icon} {chan.name} — кампании* ({cur})\n📅 {label}"]
        for c in today_camps:
            p  = compare_camps.get(c["id"], empty.copy())
            st = "🔴" if has_flags(c, p) else "🟢"
            lines.append(f"\n{st} *{c['name']}*\n{metrics_lines(c, p, cur, summary_cur)}")
        tg_send(client["telegram_chat_id"],
                f"📋 *{client['name']}*\n{'─'*30}\n" + "\n".join(lines))

# ── API ───────────────────────────────────────────────────────────────────

@app.route("/api/exchange_rates")
def api_exchange_rates():
    return jsonify(get_rates())

@app.route("/api/clients")
def api_clients():
    try:
        clients = load_clients()
        result  = []
        for c in clients:
            configured = [
                {"key": k, "name": ch.name, "icon": ch.icon,
                 "currency": c.get("channels", {}).get(k, {}).get("currency", "USD")}
                for k, ch in ALL_CHANNELS.items() if ch.is_configured(c)
            ]
            all_ch = [
                {"key": k, "name": ch.name, "icon": ch.icon,
                 "currency": c.get("channels", {}).get(k, {}).get("currency", "USD")}
                for k, ch in ALL_CHANNELS.items() if c.get("channels", {}).get(k)
            ]
            result.append({
                "id": c["id"], "name": c["name"],
                "channels": configured, "all_channels": all_ch,
                "summary_currency": c.get("summary_currency", "USD"),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/clients/<client_id>/summary")
def api_summary(client_id: str):
    date_from    = request.args.get("date_from",    (date.today() - timedelta(days=1)).isoformat())
    date_to      = request.args.get("date_to",      date_from)
    compare_from = request.args.get("compare_from", "")
    compare_to   = request.args.get("compare_to",   compare_from)

    client       = get_client_by_id(client_id)
    today_rows   = {r["channel"]: r for r in db.get_account_metrics_range(client_id, date_from, date_to)}
    compare_rows = ({r["channel"]: r for r in db.get_account_metrics_range(client_id, compare_from, compare_to)}
                   if compare_from else {})

    result = []
    for key, chan in ALL_CHANNELS.items():
        if key not in today_rows:
            continue
        y   = today_rows[key]
        p   = compare_rows.get(key, {k: 0.0 for k in y})
        cur = get_channel_currency(client, key) if client else "USD"
        result.append({"channel": key, "channel_name": chan.name, "channel_icon": chan.icon,
                        "currency": cur, "today": y, "compare": p})

    return jsonify({
        "date_from": date_from, "date_to": date_to,
        "compare_from": compare_from, "compare_to": compare_to,
        "summary_currency": get_summary_currency(client) if client else "USD",
        "channels": result,
    })

@app.route("/api/clients/<client_id>/campaigns")
def api_campaigns(client_id: str):
    channel      = request.args.get("channel",      "google_ads")
    date_from    = request.args.get("date_from",    (date.today() - timedelta(days=1)).isoformat())
    date_to      = request.args.get("date_to",      date_from)
    compare_from = request.args.get("compare_from", "")
    compare_to   = request.args.get("compare_to",   compare_from)

    client        = get_client_by_id(client_id)
    today_camps   = db.get_campaign_metrics_range(client_id, channel, date_from, date_to)
    compare_camps = ({c["id"]: c for c in db.get_campaign_metrics_range(client_id, channel, compare_from, compare_to)}
                    if compare_from else {})
    currency      = get_channel_currency(client, channel) if client else "USD"
    result        = [{"today": c, "compare": compare_camps.get(c["id"], {k: 0.0 for k in c})}
                     for c in today_camps]
    return jsonify({"campaigns": result, "currency": currency})

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    if request.headers.get("X-Admin-Token", "") != ADMIN_TOKEN:
        abort(403)
    try:
        data         = request.get_json() or {}
        date_from    = data.get("date_from", "")
        date_to      = data.get("date_to",   date_from)
        compare_from = data.get("compare_from", "")
        compare_to   = data.get("compare_to",   compare_from)

        dates: set[str] = set()
        if date_from and date_to:
            dates.update(all_dates(date_from, date_to))
        if compare_from and compare_to:
            dates.update(all_dates(compare_from, compare_to))
        if not dates:
            dates = {(date.today() - timedelta(days=1)).isoformat(),
                     (date.today() - timedelta(days=2)).isoformat()}

        job_id = str(uuid.uuid4())[:8]
        total  = len(dates)
        job_set(job_id, "running", 0, total)

        def run():
            for d in sorted(dates):
                try:
                    subprocess.run([PYTHON, "fetch_date.py", d], cwd=WORKDIR, timeout=120)
                    job_inc(job_id, total)
                except Exception as e:
                    print(f"fetch error {d}: {e}")
            job_set(job_id, "done", total, total)

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"status": "started", "job_id": job_id, "total": total})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

@app.route("/api/fetch_status/<job_id>")
def fetch_status(job_id: str):
    return jsonify(job_get(job_id))

@app.route("/api/send_telegram", methods=["POST"])
def api_send_telegram():
    if request.headers.get("X-Admin-Token", "") != ADMIN_TOKEN:
        abort(403)
    try:
        data         = request.get_json() or {}
        date_from    = data.get("date_from",    (date.today() - timedelta(days=1)).isoformat())
        date_to      = data.get("date_to",      date_from)
        compare_from = data.get("compare_from", "")
        compare_to   = data.get("compare_to",   compare_from)
        sections     = data.get("sections",     ["summary"])
        client_id    = data.get("client_id",    "")

        client = get_client_by_id(client_id)
        if not client:
            return jsonify({"status": "error", "detail": "client not found"}), 404

        build_and_send(client, date_from, date_to, compare_from, compare_to, sections)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
