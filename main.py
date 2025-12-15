import os
import base64
import requests
from email.utils import parseaddr
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# =======================
# CONFIG
# =======================
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_USER = "me"  # your personal Gmail

ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token"
    )
    service = build("gmail", "v1", credentials=creds)
    return service

# =======================
# FETCH MESSAGES
# =======================
def fetch_messages(service, max_results=50, label_ids=None):
    """Fetches recent messages from Gmail with optional label filtering."""
    messages = []
    request = service.users().messages().list(
        userId=GMAIL_USER,
        maxResults=max_results,
        labelIds=label_ids
    )

    if request is None:
        print("No messages request could be created.")
        return []

    while request:
        response = request.execute()
        msgs = response.get("messages", [])
        if not msgs:
            break
        messages.extend(msgs)
        request = service.users().messages().list_next(request, response)

    return messages

# =======================
# PARSE EMAIL
# =======================
def parse_message(service, msg_id):
    msg = service.users().messages().get(
        userId=GMAIL_USER,
        id=msg_id,
        format="full"
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
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
    elif "data" in payload["body"]:
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
def find_person_by_email(email):
    r = requests.post(
        f"{ATTIO_BASE}/objects/people/records/query",
        headers=ATTIO_HEADERS,
        json={
            "filter": {
                "email_addresses": {
                    "any": {"email_address": {"equals": email}}
                }
            }
        },
    )
    records = r.json().get("records", [])
    return records[0] if records else None

def create_person(email, name):
    if not email or "@" not in email:
        print(f"Skipping invalid email: {email}")
        return None
    
    payload = {
        "values": {
            "name": name or email,
            "email_addresses": [{"email_address": email}],
        }
    }
    
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records",
            headers=ATTIO_HEADERS,
            json=payload
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        print(f"Failed to create person for {email}: {e}, payload: {payload}")
        return None

def email_already_ingested(message_id):
    if not message_id:
        return False
    r = requests.post(
        f"{ATTIO_BASE}/objects/emails/records/query",
        headers=ATTIO_HEADERS,
        json={"filter": {"external_message_id": {"equals": message_id}}},
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
# MAIN SYNC
# =======================
def sync_personal_gmail_to_attio():
    service = get_gmail_service()
    messages = fetch_messages(service)
    
    if not messages:
        print("Mailbox is empty. No messages to sync.")
        return

    print(f"Found {len(messages)} messages")

    for m in messages:
        email = parse_message(service, m["id"])
        
        # Skip if already ingested or no message_id
        if not email["message_id"] or email_already_ingested(email["message_id"]):
            print(f"Skipping duplicate or invalid message: {email['message_id']}")
            continue
        
        person = find_person_by_email(email["from_email"])
        if not person:
            person = create_person(email["from_email"], email["from_name"])
            if person is None:
                print(f"Skipping message from invalid sender: {email['from_email']}")
                continue
        
        create_email_record(person["id"], email)
        print("Synced:", email["from_email"])

# =======================
# ENTRYPOINT
# =======================
if __name__ == "__main__":
    sync_personal_gmail_to_attio()
