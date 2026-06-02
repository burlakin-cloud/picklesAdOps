import json, os, subprocess, requests as req_lib
from datetime import date, timedelta
from flask import Flask, jsonify, render_template, request, abort
from dotenv import load_dotenv

load_dotenv()
from channels import ALL_CHANNELS
import db

app = Flask(__name__)
db.init_db()

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me")
PYTHON  = "/home/pickles_ads/venv/bin/python"
WORKDIR = "/home/pickles_ads"

def load_clients():
    with open("clients.json") as f:
        return json.load(f)

def all_dates(date_from, date_to):
    from datetime import datetime
    d0 = datetime.fromisoformat(date_from).date()
    d1 = datetime.fromisoformat(date_to).date()
    days = []
    while d0 <= d1:
        days.append(d0.isoformat())
        d0 += timedelta(days=1)
    return days

def pct_change(new, old):
    return None if old == 0 else (new - old) / old

def flag(metric, delta):
    if delta is None: return ""
    bad = (delta < 0) if metric in ("ctr","leads") else (delta > 0)
    if not bad: return ""
    return " 🔴" if abs(delta) >= 0.30 else (" ⚠️" if abs(delta) >= 0.20 else "")

def fmt_delta(delta):
    if delta is None: return "—"
    return f"{'+'if delta>=0 else ''}{delta*100:.1f}%"

def metrics_lines(y, p):
    d_spend = pct_change(y["spend"], p["spend"])
    d_ctr   = pct_change(y["ctr"],   p["ctr"])
    d_cpc   = pct_change(y["cpc"],   p["cpc"])
    d_leads = pct_change(y["leads"], p["leads"])
    return (
        f"├ Расход:  `{y['spend']:,.2f} $` ({fmt_delta(d_spend)}){flag('spend',d_spend)}\n"
        f"├ CTR:     `{y['ctr']:.2f}%` ({fmt_delta(d_ctr)}){flag('ctr',d_ctr)}\n"
        f"├ CPC:     `{y['cpc']:,.2f} $` ({fmt_delta(d_cpc)}){flag('cpc',d_cpc)}\n"
        f"├ CPM:     `{y['cpm']:,.2f} $`\n"
        f"├ Лиды:   `{y['leads']:.0f}` ({fmt_delta(d_leads)}){flag('leads',d_leads)}\n"
        f"└ CPA:     `{y['cpa']:,.2f} $`\n"
    )

def has_flags(y, p):
    return any(flag(m, pct_change(y[m], p[m])) for m in ("spend","ctr","cpc","leads"))

def tg_send(chat_id, text):
    token = os.environ.get("TG_BOT_TOKEN","")
    if not token: return
    req_lib.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=15
    )

def build_and_send(client, date_from, date_to, compare_from, compare_to, sections):
    cid = client["id"]
    empty = {"spend":0,"impressions":0,"clicks":0,"ctr":0,"cpc":0,"cpm":0,"leads":0,"cpa":0}

    if "summary" in sections:
        today_rows   = {r["channel"]: r for r in db.get_account_metrics_range(cid, date_from, date_to)}
        compare_rows = {r["channel"]: r for r in db.get_account_metrics_range(cid, compare_from, compare_to)}
        blocks = []
        for key, chan in ALL_CHANNELS.items():
            if key not in today_rows: continue
            y = today_rows[key]
            p = compare_rows.get(key, empty.copy())
            status = "🔴" if has_flags(y, p) else "🟢"
            blocks.append(f"{status} *{chan.icon} {chan.name}*\n{metrics_lines(y, p)}")
        if blocks:
            label = date_from if date_from == date_to else f"{date_from} — {date_to}"
            clabel = compare_from if compare_from == compare_to else f"{compare_from} — {compare_to}"
            msg = (f"📊 *{client['name']}*\n📅 {label} vs {clabel}\n{'─'*30}\n\n"
                   + "\n".join(blocks) + f"\n{'─'*30}\n_🔴 критично | ⚠️ внимание_")
            tg_send(client["telegram_chat_id"], msg)

    camp_keys = [s.replace("_campaigns","") for s in sections if s.endswith("_campaigns")]
    for key in camp_keys:
        chan = ALL_CHANNELS.get(key)
        if not chan: continue
        today_camps   = db.get_campaign_metrics_range(cid, key, date_from, date_to)
        compare_camps = {c["id"]: c for c in db.get_campaign_metrics_range(cid, key, compare_from, compare_to)}
        if not today_camps: continue
        lines = [f"*{chan.icon} {chan.name} — кампании*"]
        for c in today_camps:
            p  = compare_camps.get(c["id"], empty.copy())
            st = "🔴" if has_flags(c, p) else "🟢"
            lines.append(f"{st} *{c['name']}*\n{metrics_lines(c, p)}")
        label = date_from if date_from == date_to else f"{date_from} — {date_to}"
        msg = f"📋 *{client['name']}*\n📅 {label}\n{'─'*30}\n\n" + "\n".join(lines)
        tg_send(client["telegram_chat_id"], msg)


@app.route("/api/clients")
def api_clients():
    clients = load_clients()
    result = []
    for c in clients:
        ch = [{"key":k,"name":ch.name,"icon":ch.icon}
              for k,ch in ALL_CHANNELS.items() if ch.is_configured(c)]
        result.append({"id":c["id"],"name":c["name"],"channels":ch})
    return jsonify(result)


@app.route("/api/clients/<client_id>/summary")
def api_summary(client_id):
    date_from    = request.args.get("date_from", (date.today()-timedelta(days=1)).isoformat())
    date_to      = request.args.get("date_to",   date_from)
    compare_from = request.args.get("compare_from", (date.today()-timedelta(days=2)).isoformat())
    compare_to   = request.args.get("compare_to",   compare_from)

    today_rows   = {r["channel"]:r for r in db.get_account_metrics_range(client_id, date_from, date_to)}
    compare_rows = {r["channel"]:r for r in db.get_account_metrics_range(client_id, compare_from, compare_to)}

    result = []
    for key, chan in ALL_CHANNELS.items():
        if key not in today_rows: continue
        y = today_rows[key]
        p = compare_rows.get(key, {k:0.0 for k in y})
        result.append({"channel":key,"channel_name":chan.name,"channel_icon":chan.icon,
                        "today":y,"compare":p})
    return jsonify({"date_from":date_from,"date_to":date_to,
                    "compare_from":compare_from,"compare_to":compare_to,"channels":result})


@app.route("/api/clients/<client_id>/campaigns")
def api_campaigns(client_id):
    channel      = request.args.get("channel","google_ads")
    date_from    = request.args.get("date_from", (date.today()-timedelta(days=1)).isoformat())
    date_to      = request.args.get("date_to",   date_from)
    compare_from = request.args.get("compare_from", (date.today()-timedelta(days=2)).isoformat())
    compare_to   = request.args.get("compare_to",   compare_from)

    today_camps   = db.get_campaign_metrics_range(client_id, channel, date_from, date_to)
    compare_camps = {c["id"]:c for c in db.get_campaign_metrics_range(client_id, channel, compare_from, compare_to)}
    result = [{"today":c,"compare":compare_camps.get(c["id"],{k:0.0 for k in c})} for c in today_camps]
    return jsonify({"campaigns":result})


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    if request.headers.get("X-Admin-Token","") != ADMIN_TOKEN: abort(403)
    try:
        data         = request.get_json() or {}
        date_from    = data.get("date_from","")
        date_to      = data.get("date_to", date_from)
        compare_from = data.get("compare_from","")
        compare_to   = data.get("compare_to", compare_from)

        dates = set()
        if date_from and date_to:
            dates.update(all_dates(date_from, date_to))
        if compare_from and compare_to:
            dates.update(all_dates(compare_from, compare_to))
        if not dates:
            dates = {(date.today()-timedelta(days=1)).isoformat(),
                     (date.today()-timedelta(days=2)).isoformat()}

        for d in sorted(dates):
            subprocess.run([PYTHON,"fetch_date.py",d], cwd=WORKDIR, timeout=120)

        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","detail":str(e)}), 500


@app.route("/api/send_telegram", methods=["POST"])
def api_send_telegram():
    if request.headers.get("X-Admin-Token","") != ADMIN_TOKEN: abort(403)
    try:
        data = request.get_json() or {}
        date_from    = data.get("date_from",    (date.today()-timedelta(days=1)).isoformat())
        date_to      = data.get("date_to",      date_from)
        compare_from = data.get("compare_from", (date.today()-timedelta(days=2)).isoformat())
        compare_to   = data.get("compare_to",   compare_from)
        sections     = data.get("sections",     ["summary"])
        client_id    = data.get("client_id",    "")

        clients = load_clients()
        client  = next((c for c in clients if c["id"]==client_id), None)
        if not client: return jsonify({"status":"error","detail":"client not found"}), 404

        build_and_send(client, date_from, date_to, compare_from, compare_to, sections)
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","detail":str(e)}), 500


@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
