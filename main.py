import os
import base64
import requests
from email.utils import parseaddr
from google.oauth2 import service_account
from googleapiclient.discovery import build

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"  # path to service account JSON
REAL_USER_EMAIL = "kadidia.sylla@mithrildefense.com"                             # Gmail user with read access
ATTIO_INBOUND = "mithrildefense@attio.email"                               # Attio inbound email
ATTIO_TOKEN = os.environ.get("ATTIO_TOKEN")                                # Set this in env

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}

# Keep track of already-forwarded message IDs
PROCESSED_MESSAGES_FILE = "/home/ec2-user/.processed_emails.txt"

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=REAL_USER_EMAIL
    )
    return build("gmail", "v1", credentials=creds)

# =======================
# FETCH MESSAGES
# =======================
def fetch_messages(service, max_results=50):
    results = service.users().messages().list(userId="me", maxResults=max_results).execute()
    return results.get("messages", [])

# =======================
# PARSE EMAIL
# =======================
def parse_message(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    
    sender_name, sender_email = parseaddr(headers.get("From", ""))
    subject = headers.get("Subject", "(no subject)")
    message_id = headers.get("Message-ID")
    
    # Ignore system emails
    if sender_email.lower().endswith("noreply@google.com") or sender_email.lower().endswith("noreply@"):
        return None

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
        "subject": subject,
        "from_email": sender_email,
        "body": body,
        "message_id": message_id
    }

# =======================
# DUPLICATE CHECK
# =======================
def load_processed():
    if not os.path.exists(PROCESSED_MESSAGES_FILE):
        return set()
    with open(PROCESSED_MESSAGES_FILE, "r") as f:
        return set(line.strip() for line in f.readlines())

def save_processed(message_id):
    with open(PROCESSED_MESSAGES_FILE, "a") as f:
        f.write(message_id + "\n")

# =======================
# POST TO ATTIO
# =======================
def forward_to_attio(email):
    payload = {
        "to": ATTIO_INBOUND,
        "subject": email["subject"],
        "from": email["from_email"],
        "body": email["body"]
    }
    r = requests.post("https://api.attio.com/v2/forward", headers=ATTIO_HEADERS, json=payload)
    r.raise_for_status()
    print("Forwarded to Attio:", email["subject"])

# =======================
# MAIN SYNC
# =======================
def main():
    service = get_gmail_service()
    messages = fetch_messages(service)
    print(f"Found {len(messages)} messages")

    processed = load_processed()

    for m in messages:
        email = parse_message(service, m["id"])
        if not email or not email.get("message_id"):
            continue
        if email["message_id"] in processed:
            print("Skipping duplicate:", email["subject"])
            continue

        try:
            forward_to_attio(email)
            save_processed(email["message_id"])
        except requests.exceptions.HTTPError as e:
            print(f"Failed to forward {email['from_email']}: {e}")

if __name__ == "__main__":
    main()
