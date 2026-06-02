"""
app.py — Flask веб-приложение.
Запуск: python app.py
Продакшн: gunicorn app:app
"""
import json
import os
import subprocess
from datetime import date, timedelta
from flask import Flask, jsonify, render_template, request, abort
from dotenv import load_dotenv

load_dotenv()

from channels import ALL_CHANNELS
import db

app = Flask(__name__)
db.init_db()

# Простой токен для защиты ручного запуска фетчера
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me")


def load_clients():
    with open("clients.json") as f:
        return json.load(f)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/clients")
def api_clients():
    clients = load_clients()
    result = []
    for c in clients:
        active_channels = [
            {"key": k, "name": ch.name, "icon": ch.icon}
            for k, ch in ALL_CHANNELS.items()
            if ch.is_configured(c)
        ]
        result.append({
            "id": c["id"],
            "name": c["name"],
            "channels": active_channels,
        })
    return jsonify(result)


@app.route("/api/clients/<client_id>/summary")
def api_summary(client_id):
    d = request.args.get("date", (date.today() - timedelta(days=1)).isoformat())
    compare = request.args.get("compare", (date.today() - timedelta(days=2)).isoformat())

    today_rows    = {r["channel"]: r for r in db.get_account_metrics(client_id, d)}
    compare_rows  = {r["channel"]: r for r in db.get_account_metrics(client_id, compare)}

    result = []
    for key, chan in ALL_CHANNELS.items():
        if key not in today_rows:
            continue
        y = today_rows[key]
        p = compare_rows.get(key, {k: 0.0 for k in y})
        result.append({
            "channel": key,
            "channel_name": chan.name,
            "channel_icon": chan.icon,
            "today": y,
            "compare": p,
        })

    return jsonify({"date": d, "compare": compare, "channels": result})


@app.route("/api/clients/<client_id>/campaigns")
def api_campaigns(client_id):
    channel = request.args.get("channel", "google_ads")
    d       = request.args.get("date", (date.today() - timedelta(days=1)).isoformat())
    compare = request.args.get("compare", (date.today() - timedelta(days=2)).isoformat())

    today_camps   = db.get_campaign_metrics(client_id, channel, d)
    compare_camps = {c["id"]: c for c in db.get_campaign_metrics(client_id, channel, compare)}

    result = []
    for c in today_camps:
        p = compare_camps.get(c["id"], {k: 0.0 for k in c})
        result.append({"today": c, "compare": p})

    return jsonify({"date": d, "compare": compare, "campaigns": result})


@app.route("/api/clients/<client_id>/trend")
def api_trend(client_id):
    channel = request.args.get("channel", "google_ads")
    days    = int(request.args.get("days", 7))
    return jsonify(db.get_trend(client_id, channel, days))


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    """Ручной запуск фетчера. Защищён токеном."""
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        abort(403)
    try:
        subprocess.Popen(["python", "fetcher.py"])
        return jsonify({"status": "started"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
