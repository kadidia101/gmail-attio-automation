import os
import base64
import logging
from email.utils import parseaddr
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests
from datetime import datetime
import time
from google.oauth2.service_account import Credentials


# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GROUP_EMAIL = "kadidia.sylla@mithrildefense.com"  # Your Google Group
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
headers = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ----------------------
# ATTIO HELPERS
# ----------------------

def assert_person(name, primary_email):
    payload = {
        "values": {
            "name": name,
            "primary_email_address": primary_email,
            "email_addresses": [{"email_address": primary_email}]
        }
    }
    url = f"{ATTIO_BASE}/people/records/assert"
    try:
        r = requests.post(url, headers=HEADERS, json=payload)
        r.raise_for_status()
        person_data = r.json()["data"]
        logging.info("Person asserted | email=%s | record_id=%s", primary_email, person_data.get("record_id"))
        return person_data
    except requests.HTTPError as e:
        logging.warning("Cannot assert person %s: %s", primary_email, e.response.text)
        return None

def create_email_record(person_id, email):
    payload = {
        "values": {
            "subject": email["subject"],
            "body": email["body"],
            "from": email["from_email"],
            "external_message_id": email["message_id"],
            "person": [{"target_object": "people", "target_record_id": person_id}]
        }
    }
    url = f"{ATTIO_BASE}/emails/records"
    try:
        r = requests.post(url, headers=HEADERS, json=payload)
        r.raise_for_status()
        logging.info("Synced email from %s", email["from_email"])
        return r.json()
    except requests.HTTPError as e:
        logging.warning("Cannot create email record for %s: %s", email["from_email"], e.response.text)
        return None

def get_company_by_domain(domain):
    payload = {"filter": {"domain": {"equals": domain}}, "limit": 1}
    url = f"{ATTIO_BASE}/companies/records/query"
    try:
        r = requests.post(url, headers=HEADERS, json=payload)
        r.raise_for_status()
        data = r.json().get("data")
        if data:
            return data[0]["record_id"]
        return None
    except requests.HTTPError as e:
        logging.warning("Cannot find company for domain %s: %s", domain, e.response.text)
        return None

# ----------------------
# GMAIL HELPERS
# ----------------------

def get_gmail_service():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build("gmail", "v1", credentials=creds)
    return service

def fetch_group_messages(service, max_results=50):
    query = f"to:{GROUP_EMAIL}"
    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])
    logging.info("Found %d messages", len(messages))
    detailed_messages = []
    for msg in messages:
        m = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        payload = m["payload"]
        headers = payload["headers"]
        email_from = next((h["value"] for h in headers if h["name"] == "From"), None)
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    body = part["body"].get("data", "")
                    break
        detailed_messages.append({
            "from_email": email_from,
            "from_name": email_from.split("<")[0].strip() if "<" in email_from else email_from,
            "subject": subject,
            "body": body,
            "message_id": m["id"]
        })
    return detailed_messages

# ----------------------
# SYNC LOOP
# ----------------------

def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    messages = fetch_group_messages(service, max_results=max_results)

    for email in messages:
        # Only process emails from the group itself
        if GROUP_EMAIL.lower() not in email["from_email"].lower():
            logging.info("Skipping email not from group: %s", email["from_email"])
            continue

        # Create or assert person
        person = assert_person(email["from_name"], email["from_email"])
        if not person:
            logging.warning("Skipping email — person unavailable: %s", email["from_email"])
            continue

        person_id = person.get("record_id")
        if not person_id:
            logging.warning("Skipping email — no record_id for person: %s", email["from_email"])
            continue

        # Create email record linked to person
        create_email_record(person_id, email)

        # Link company if possible
        domain = email["from_email"].split("@")[-1]
        company_id = get_company_by_domain(domain)
        if company_id:
            logging.info("Linked person %s to company %s", email["from_email"], domain)

# ----------------------
# MAIN LOOP
# ----------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            sync_group_to_attio(max_results=50)
        except Exception as e:
            logging.error("Error during sync: %s", e)
        logging.info("Sleeping 5 minutes...")
        time.sleep(300)  # 5 minutes
