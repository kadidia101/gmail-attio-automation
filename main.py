import os
import base64
import requests
from email.utils import parseaddr
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# =======================
# CONFIG
# =======================

GROUP_EMAIL = os.environ["GROUP_EMAIL"]

GMAIL_CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
ATTIO_BASE = "https://api.attio.com/v2"

ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}

# =======================
# GMAIL CLIENT (OAUTH)
# =======================

def get_group_gmail_service():
    creds = Credentials(
        None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        scopes=GMAIL_SCOPES,
    )

    return build("gmail", "v1", credentials=creds)

# =======================
# FETCH GROUP EMAILS
# =======================

def fetch_group_messages(service, max_results=50):
    res = service.users().messages().list(
        userId="me",
        q=f"to:{GROUP_EMAIL}",
        maxResults=max_results,
    ).execute()

    return res.get("messages", [])

# =======================
# PARSE EMAIL
# =======================

def parse_message(service, msg_id):
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full",
    ).execute()

    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}

    sender_name, sender_email = parseaddr(headers.get("From", ""))
    subject = headers.get("Subject", "")
    message_id = headers.get("Message-ID")

    body = ""
    payload = msg["payload"]

    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and "data" in part["body"]:
                body = base64.urlsafe_b64decode(
                    part["body"]["data"]
                ).decode("utf-8", errors="ignore")
                break
    elif "data" in payload["body"]:
        body = base64.urlsafe_b64decode(
            payload["body"]["data"]
        ).decode("utf-8", errors="ignore")

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

def find_person_by_email(email):
    r = requests.post(
        f"{ATTIO_BASE}/objects/people/records/query",
        headers=ATTIO_HEADERS,
        json={
            "filter": {
                "email_addresses": {
                    "any": {
                        "email_address": {"equals": email}
                    }
                }
            }
        },
    )
    records = r.json().get("records", [])
    return records[0] if records else None

def create_person(email, name):
    r = requests.post(
        f"{ATTIO_BASE}/objects/people/records",
        headers=ATTIO_HEADERS,
        json={
            "values": {
                "name": name or email,
                "email_addresses": [{"email_address": email}],
            }
        },
    )
    r.raise_for_status()
    return r.json()

def email_already_ingested(message_id):
    if not message_id:
        return False

    r = requests.post(
        f"{ATTIO_BASE}/objects/emails/records/query",
        headers=ATTIO_HEADERS,
        json={
            "filter": {
                "external_message_id": {"equals": message_id}
            }
        },
    )
    return len(r.json().get("records", [])) > 0

def create_email_record(person_id, email):
    r = requests.post(
        f"{ATTIO_BASE}/objects/emails/records",
        headers=ATTIO_HEADERS,
        json={
            "values": {
                "subject": email["subject"],
                "body": email["body"],
                "from": email["from_email"],
                "external_message_id": email["message_id"],
                "person": [{
                    "target_object": "people",
                    "target_record_id": person_id
                }],
            }
        },
    )
    r.raise_for_status()
    return r.json()

# =======================
# MAIN SYNC LOOP
# =======================

def sync_group_to_attio():
    service = get_group_gmail_service()
    messages = fetch_group_messages(service)

    print(f"Found {len(messages)} messages")

    for m in messages:
        email = parse_message(service, m["id"])

        if not email["message_id"]:
            continue

        if email_already_ingested(email["message_id"]):
            continue

        person = find_person_by_email(email["from_email"])
        if not person:
            person = create_person(
                email["from_email"],
                email["from_name"],
            )

        create_email_record(person["id"], email)
        print("Synced:", email["from_email"])

# =======================
# ENTRYPOINT
# =======================

if __name__ == "__main__":
    sync_group_to_attio()
