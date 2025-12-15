import os
import base64
import requests
from email.utils import parseaddr
from google.oauth2 import service_account
from googleapiclient.discovery import build

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GROUP_EMAIL = "kadidia.sylla@mithrildefense.com"  # user in the workspace
ATTIO_INBOUND = "mithrildefense@attio.email"

ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GROUP_EMAIL  # impersonate the user
    )
    return build("gmail", "v1", credentials=creds)

# =======================
# FETCH MESSAGES
# =======================
def fetch_messages(service, max_results=50):
    res = service.users().messages().list(userId="me", maxResults=max_results).execute()
    return res.get("messages", [])

# =======================
# PARSE EMAIL
# =======================
def parse_message(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    sender_name, sender_email = parseaddr(headers.get("From", ""))
    subject = headers.get("Subject", "")
    message_id = headers.get("Message-ID", None)

    # decode body
    body = ""
    payload = msg["payload"]
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part["body"]:
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
    elif "data" in payload["body"]:
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    return {
        "from_name": sender_name,
        "from_email": sender_email,
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }

# =======================
# ATTIO POST
# =======================
def email_already_ingested(message_id):
    if not message_id:
        return False
    r = requests.post(
        "https://api.attio.com/v2/objects/emails/records/query",
        headers=ATTIO_HEADERS,
        json={"filter": {"external_message_id": {"equals": message_id}}},
    )
    return len(r.json().get("records", [])) > 0

def forward_to_attio(email):
    if email_already_ingested(email["message_id"]):
        print("Skipping duplicate:", email["message_id"])
        return
    payload = {
        "to": ATTIO_INBOUND,
        "from_name": email["from_name"],
        "from_email": email["from_email"],
        "subject": email["subject"],
        "body": email["body"],
        "external_message_id": email["message_id"]
    }
    r = requests.post("https://api.attio.com/v2/forward", headers=ATTIO_HEADERS, json=payload)
    r.raise_for_status()
    print("Forwarded:", email["message_id"])

# =======================
# MAIN
# =======================
def main():
    service = get_gmail_service()
    messages = fetch_messages(service)
    print(f"Found {len(messages)} messages")
    for m in messages:
        email = parse_message(service, m["id"])
        if not email["message_id"]:
            print("Skipping message with no Message-ID:", email)
            continue
        forward_to_attio(email)

if __name__ == "__main__":
    main()
