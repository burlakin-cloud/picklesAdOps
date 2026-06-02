import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "data/metrics.db")

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _sdiv(a, b):
    return a / b if b else 0.0

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS account_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL, channel TEXT NOT NULL, report_date TEXT NOT NULL,
                spend REAL, impressions INTEGER, clicks INTEGER,
                ctr REAL, cpc REAL, cpm REAL, leads REAL, cpa REAL,
                UNIQUE(client_id, channel, report_date)
            );
            CREATE TABLE IF NOT EXISTS campaign_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL, channel TEXT NOT NULL, report_date TEXT NOT NULL,
                campaign_id TEXT NOT NULL, campaign_name TEXT,
                spend REAL, impressions INTEGER, clicks INTEGER,
                ctr REAL, cpc REAL, cpm REAL, leads REAL, cpa REAL,
                UNIQUE(client_id, channel, report_date, campaign_id)
            );
        """)

def upsert_account(client_id, channel, date, m):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO account_metrics
                (client_id, channel, report_date, spend, impressions, clicks, ctr, cpc, cpm, leads, cpa)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (client_id, channel, date,
              m["spend"], m["impressions"], m["clicks"],
              m["ctr"], m["cpc"], m["cpm"], m["leads"], m["cpa"]))

def upsert_campaigns(client_id, channel, date, campaigns):
    with get_conn() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO campaign_metrics
                (client_id, channel, report_date, campaign_id, campaign_name,
                 spend, impressions, clicks, ctr, cpc, cpm, leads, cpa)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(client_id, channel, date, c["id"], c["name"],
               c["spend"], c["impressions"], c["clicks"],
               c["ctr"], c["cpc"], c["cpm"], c["leads"], c["cpa"])
              for c in campaigns])

def _calc(d):
    s = d.get("spend") or 0.0
    c = d.get("clicks") or 0.0
    i = d.get("impressions") or 0.0
    l = d.get("leads") or 0.0
    d["spend"] = s; d["clicks"] = c; d["impressions"] = i; d["leads"] = l
    d["ctr"] = _sdiv(c, i) * 100
    d["cpc"] = _sdiv(s, c)
    d["cpm"] = _sdiv(s, i) * 1000
    d["cpa"] = _sdiv(s, l)
    return d

def get_account_metrics(client_id, date):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT channel, spend, impressions, clicks, ctr, cpc, cpm, leads, cpa
            FROM account_metrics WHERE client_id=? AND report_date=?
        """, (client_id, date)).fetchall()
    return [dict(r) for r in rows]

def get_account_metrics_range(client_id, date_from, date_to):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT channel,
                   SUM(spend) as spend, SUM(impressions) as impressions,
                   SUM(clicks) as clicks, SUM(leads) as leads
            FROM account_metrics
            WHERE client_id=? AND report_date BETWEEN ? AND ?
            GROUP BY channel
        """, (client_id, date_from, date_to)).fetchall()
    return [_calc(dict(r)) for r in rows]

def get_campaign_metrics(client_id, channel, date):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT campaign_id as id, campaign_name as name,
                   spend, impressions, clicks, ctr, cpc, cpm, leads, cpa
            FROM campaign_metrics
            WHERE client_id=? AND channel=? AND report_date=?
            ORDER BY spend DESC
        """, (client_id, channel, date)).fetchall()
    return [dict(r) for r in rows]

def get_campaign_metrics_range(client_id, channel, date_from, date_to):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT campaign_id as id, campaign_name as name,
                   SUM(spend) as spend, SUM(impressions) as impressions,
                   SUM(clicks) as clicks, SUM(leads) as leads
            FROM campaign_metrics
            WHERE client_id=? AND channel=? AND report_date BETWEEN ? AND ?
            GROUP BY campaign_id, campaign_name
            ORDER BY spend DESC
        """, (client_id, channel, date_from, date_to)).fetchall()
    return [_calc(dict(r)) for r in rows]

def get_trend(client_id, channel, days=7):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT report_date as date, spend, ctr, cpc, cpm, leads, cpa
            FROM account_metrics
            WHERE client_id=? AND channel=?
            ORDER BY report_date DESC LIMIT ?
        """, (client_id, channel, days)).fetchall()
    return [dict(r) for r in reversed(rows)]
