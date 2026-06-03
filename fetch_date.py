import sys
import json
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

from channels import ALL_CHANNELS
import db

def fetch_for_date(target_date):
    db.init_db()
    with open("clients.json") as f:
        clients = json.load(f)
    for client in clients:
        cid = client["id"]
        log.info(f"== {client['name']} — {target_date} ==")
        for key, channel in ALL_CHANNELS.items():
            if not channel.is_configured(client):
                continue
            log.info(f"  {channel.name}...")
            acc = channel.fetch_account(client, target_date)
            if acc:
                db.upsert_account(cid, key, target_date, acc)
                log.info(f"  spend={acc['spend']:.2f}")
            camps = channel.fetch_campaigns(client, target_date)
            if camps:
                db.upsert_campaigns(cid, key, target_date, camps)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажи дату: python fetch_date.py 2026-05-25")
        sys.exit(1)
    fetch_for_date(sys.argv[1])
