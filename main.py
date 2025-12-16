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

IGNORE_EMAILS = {
    GROUP_EMAIL,
    "automation@mithrildefense.com"
}

ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# =======================
# GMAIL
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GROUP_EMAIL,
    )
    return build("gmail", "v1", credentials=creds)

def fetch_messages(service, max_results=50):
    try:
        query = f"to:automation@mithrildefense.com"
        res = service.users().messages().list(userId="me", maxResults=max_results, q=query).execute()
        return res.get("messages", [])
    except Exception as e:
        logging.error("Failed to fetch messages: %s", e)
        return []

def parse_message(service, msg_id):

    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full",
    ).execute()

    headers = {
        h["name"].lower(): h["value"]
        for h in msg["payload"]["headers"]
    }

    from_name, from_email = parseaddr(headers.get("from", ""))

    subject = headers.get("subject", "")
    message_id = headers.get("message-id")

    logging.info("RAW HEADERS DUMP:")
    for k, v in headers.items():
        if any(x in k for x in [
            "from",
            "reply",
            "sender",
            "return",
            "original",
            "google",
        ]):
            logging.info("  %s: %s", k, v)
            
    return {
        "from_name": from_name,
        "from_email": from_email.lower(),
        "headers": headers,
        "subject": subject,
        "message_id": message_id,
    }


def extract_recipients(headers):
    recipients = set()
    for field in ["to", "cc", "bcc"]:
        if field in headers:
            for _, email in getaddresses([headers[field]]):
                email = email.lower()
                if email:
                    recipients.add(email)
    return recipients


def get_domain(email):
    return email.split("@")[-1].lower()

# =======================
# ATTIO
# =======================
def extract_record_id(resp_json):
    return (
        resp_json.get("record_id")
        or resp_json.get("data", {}).get("record_id")
        or resp_json.get("data", {}).get("id", {}).get("record_id")
        or resp_json.get("data", {})
            .get("values", {})
            .get("record_id", [{}])[0]
            .get("value")
    )


def assert_person_with_company(name, email):
    if email in IGNORE_EMAILS:
        logging.info("Skipping ignored email: %s", email)
        return None

    domain = get_domain(email)

    if " " in name.strip():
        first, last = name.split(" ", 1)
    else:
        first, last = name or email.split("@")[0], ""

    payload = {
        "data": {
            "values": {
                "name": [{
                    "first_name": first,
                    "last_name": last,
                    "full_name": f"{first} {last}".strip(),
                }],
                "email_addresses": [{
                    "email_address": email,
                }],
                "company": [{
                    "target_object": "companies",
                    "domains": [{"domain": domain}],
                }],
            }
        }
    }

    url = f"{ATTIO_BASE}/objects/people/records?matching_attribute=email_addresses"

    resp = requests.put(url, headers=ATTIO_HEADERS, json=payload)

    if resp.status_code in (200, 201):
        record_id = extract_record_id(resp.json())
        logging.info(
            "Asserted person | %s <%s> | record_id=%s",
            name,
            email,
            record_id,
        )
        return record_id

    logging.warning("Failed to assert %s: %s", email, resp.text)
    return None

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    messages = fetch_messages(service, max_results)

    logging.info("Found %d messages", len(messages))

    for m in messages:
        msg = parse_message(service, m["id"])

        logging.info("Processing message ID: %s", msg["message_id"])
        logging.info("FROM header: %s <%s>", msg["from_name"], msg["from_email"])
        logging.info("Subject: %s", msg["subject"])

        participants = {}

        # Sender
        if msg["from_email"] and msg["from_email"] not in IGNORE_EMAILS:
            participants[msg["from_email"]] = msg["from_name"]

        # Recipients
        for r in extract_recipients(msg["headers"]):
            participants.setdefault(r, r.split("@")[0])

        logging.info("Participants resolved: %s", list(participants.keys()))

        for email, name in participants.items():
            assert_person_with_company(name=name, email=email)

        logging.info("Finished message %s", msg["message_id"])

# =======================
# LOOP
# =======================
if __name__ == "__main__":
    while True:
        try:
            logging.info("Starting Gmail → Attio sync")
            sync_group_to_attio(max_results=100)
            logging.info("Sync complete — sleeping 5 minutes")
        except Exception as e:
            logging.exception("Sync failed")
        time.sleep(300)
