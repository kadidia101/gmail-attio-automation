import os
import time
import base64
import logging
from email.utils import parseaddr, getaddresses
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GROUP_EMAIL = "kadidia.sylla@mithrildefense.com"
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

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

IGNORE_EMAILS = {GROUP_EMAIL, "automation@mithrildefense.com"}

def parse_message(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

    # Try to find original sender
    original_from = headers.get("x-original-from") or headers.get("return-path") or headers.get("from", "")
    sender_name, sender_email = parseaddr(original_from)

    subject = headers.get("subject", "")
    message_id = headers.get("message-id")

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
        "headers": headers,
    }

# In the sync loop:
if email["from_email"] in IGNORE_EMAILS:
    logging.info("Skipping email from automation address: %s", email["from_email"])
    continue

def get_domain(email):
    return email.split("@")[-1].lower()

def get_all_recipients(headers):
    recipients = []
    for field in ["To", "Cc", "Bcc"]:
        if field in headers:
            recipients.extend([email.lower() for name, email in getaddresses([headers[field]])])
    return recipients

# =======================
# ATTIO HELPERS
# =======================
def extract_record_id(resp_json):
    record_id = resp_json.get("record_id")
    if record_id:
        return record_id
    record_id = resp_json.get("data", {}).get("record_id")
    if record_id:
        return record_id
    record_id = resp_json.get("data", {}).get("id", {}).get("record_id")
    if record_id:
        return record_id
    try:
        record_id = resp_json.get("data", {}).get("values", {}).get("record_id", [{}])[0].get("value")
        if record_id:
            return record_id
    except Exception:
        pass
    logging.warning("Could not find record_id in response: %s", resp_json)
    return None

def assert_person_with_company(name, email):
    domain = get_domain(email)

    if " " in name.strip():
        first_name, last_name = name.strip().split(" ", 1)
    else:
        first_name, last_name = name.strip(), ""

    payload = {
        "data": {
            "values": {
                "name": [{"first_name": first_name, "last_name": last_name, "full_name": name}],
                "email_addresses": [{"email_address": email}],
                "company": [{"target_object": "companies", "domains":[{"domain": domain}]}]
            }
        }
    }

    url = f"{ATTIO_BASE}/objects/people/records?matching_attribute=email_addresses"
    resp = requests.put(url, headers=ATTIO_HEADERS, json=payload)

    if resp.status_code in (200, 201):
        person_json = resp.json()
        record_id = extract_record_id(person_json)
        logging.info("Asserted person | email=%s | record_id=%s | company_domain=%s",
                     email, record_id, domain)
        person_json["record_id"] = record_id
        return person_json
    else:
        logging.warning("Failed to assert person %s: %s", email, resp.text)
        return None

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    messages = fetch_messages(service, max_results)
    logging.info("Found %d messages", len(messages))

    for m in messages:
        email_msg = parse_message(service, m["id"])
        logging.info("Processing message ID: %s", email_msg["message_id"])

        if GROUP_EMAIL not in email_msg["from_email"]:
            logging.info("Skipping email not from group: %s", email_msg["from_email"])
            continue
        if not email_msg["message_id"]:
            logging.warning("Skipping message with no Message-ID: %s", email_msg)
            continue

        # Gather all involved emails: sender + recipients
        all_emails = [email_msg["from_email"]] + get_all_recipients(email_msg["headers"])

        for e in all_emails:
            person = assert_person_with_company(name=e.split("@")[0], email=e)
            if not person:
                logging.warning("Could not assert person: %s", e)
            # No need to create email records — Attio handles it automatically

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
