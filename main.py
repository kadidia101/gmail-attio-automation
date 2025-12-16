import os
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GROUP_EMAIL  # impersonate the group mailbox
    )
    service = build("gmail", "v1", credentials=creds)
    return service

def fetch_messages(service, max_results=50):
    """Fetch only messages sent from the Google Group."""
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
        "from_email": sender_email,
        "from_name": sender_name,
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }

# =======================
# ATTIO HELPERS
# =======================
def create_person(name, email):
    data = {
        "values": {
            "name": name,
            "email_addresses": [{"email_address": email}]
        }
    }
    r = requests.post(
        "https://api.attio.com/v2/objects/people/records",
        headers=headers,
        json=data
    )
    
    if r.status_code != 200:
        logging.warning("Cannot create person %s: %s", email, r.text)
        return None

    resp_json = r.json()
    record_id = resp_json.get("record_id")
    logging.info("Created person | email=%s | record_id=%s", email, record_id)
    return resp_json

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

        # Filter only emails from the Google Group
        if GROUP_EMAIL not in email["from_email"]:
            logging.info("Skipping email not from group: %s", email["from_email"])
            continue

        if not email["message_id"]:
            logging.warning("Skipping message with no Message-ID: %s", email)
            continue

        # Attempt to create person
        person = create_person(email["from_name"], email["from_email"])
        if not person:
            logging.warning("Skipping person creation — already exists or cannot create: %s", email["from_email"])
            continue
        
        person_id = person.get("record_id")


        create_email_record(person_id, email)

# =======================
# ENTRYPOINT
# =======================
if __name__ == "__main__":
    sync_group_to_attio(max_results=100)
