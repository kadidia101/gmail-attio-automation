import os
import time
import base64
import logging
from email.utils import parseaddr
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GROUP_EMAIL = "kadidia.sylla@mithrildefense.com"  # Your Google Group
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

COMPANY_DOMAIN_FIELD = "domains"  # Attio company domain field (multiselect)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GROUP_EMAIL
    )
    return build("gmail", "v1", credentials=creds)

def fetch_messages(service, max_results=50):
    try:
        query = f"from:{GROUP_EMAIL}"
        res = service.users().messages().list(userId="me", maxResults=max_results, q=query).execute()
        return res.get("messages", [])
    except Exception as e:
        logging.error("Failed to fetch messages: %s", e)
        return []

def parse_message(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    sender_name, sender_email = parseaddr(headers.get("From", ""))
    subject = headers.get("Subject", "")
    message_id = headers.get("Message-ID")

    body = ""
    payload = msg.get("payload", {})
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
    elif "body" in payload and "data" in payload["body"]:
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    return {
        "from_email": sender_email.lower(),
        "from_name": sender_name,
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }

# =======================
# ATTIO HELPERS
# =======================
def get_domain(email):
    return email.split("@")[-1].lower()

def get_or_create_company(domain):
    # Query existing company
    query_payload = {"filter": {"property": COMPANY_DOMAIN_FIELD, "values": [domain]}}
    resp = requests.post(f"{ATTIO_BASE}/objects/companies/query", headers=ATTIO_HEADERS, json=query_payload)
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        if results:
            return results[0]["record_id"]

    # Create new company
    data = {"data": {"values": {"name": domain, COMPANY_DOMAIN_FIELD: [domain]}}}
    create_resp = requests.post(f"{ATTIO_BASE}/objects/companies/records", headers=ATTIO_HEADERS, json=data)
    if create_resp.status_code == 200:
        return create_resp.json().get("record_id")

    # If uniqueness conflict occurs, refetch
    if create_resp.status_code == 400 and create_resp.json().get("code") == "uniqueness_conflict":
        logging.info("Company already exists | domain=%s. Refetching...", domain)
        resp2 = requests.post(f"{ATTIO_BASE}/objects/companies/query", headers=ATTIO_HEADERS, json=query_payload)
        if resp2.status_code == 200:
            results2 = resp2.json().get("results", [])
            if results2:
                return results2[0]["record_id"]

    logging.warning("Failed to create company for domain %s: %s", domain, create_resp.text)
    return None

def assert_person(name, email, domain=None):
    """
    Asserts a person record in Attio. Automatically creates/updates person and company.
    """
    payload = {
        "data": {
            "values": {
                "name": [{"full_name": name}],
                "email_addresses": [{"email_address": email}],
            }
        }
    }

    if domain:
        # attach company by domain inline
        payload["data"]["values"]["company"] = [
            {"domains": [{"domain": domain}]}
        ]

    resp = requests.put(f"{ATTIO_BASE}/objects/people/records", headers=ATTIO_HEADERS, json=payload)

    if resp.status_code in (200, 201):
        person = resp.json()
        logging.info("Asserted person | email=%s | record_id=%s", email, person.get("record_id"))
        return person
    else:
        logging.warning("Failed to assert person %s: %s", email, resp.text)
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
    r = requests.post(f"{ATTIO_BASE}/objects/emails/records", headers=ATTIO_HEADERS, json=payload)
    try:
        r.raise_for_status()
        logging.info("Synced email from %s", email["from_email"])
        return r.json()
    except requests.HTTPError as e:
        logging.warning("Cannot create email record for %s: %s", email["from_email"], e.response.text)
        return None

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    messages = fetch_messages(service, max_results)
    logging.info("Found %d messages", len(messages))

    for m in messages:
        email = parse_message(service, m["id"])
        logging.info("Processing message ID: %s", email["message_id"])

        # Only emails from the group
        if GROUP_EMAIL not in email["from_email"]:
            logging.info("Skipping email not from group: %s", email["from_email"])
            continue
        if not email["message_id"]:
            logging.warning("Skipping message with no Message-ID: %s", email)
            continue

        company_id = get_or_create_company(get_domain(email["from_email"]))
        person = assert_person(email["from_name"], email["from_email"], company_id)

        if not person:
            logging.warning("Skipping email because no person record for %s", email["from_email"])
            continue

        create_email_record(person["record_id"], email)

# =======================
# ENTRYPOINT (5-min loop)
# =======================
if __name__ == "__main__":
    while True:
        try:
            logging.info("Starting Gmail → Attio sync...")
            sync_group_to_attio(max_results=100)
            logging.info("Sync complete. Waiting 5 minutes...")
        except Exception as e:
            logging.error("Error during sync: %s", e)
        time.sleep(300)  # 5 minutes
