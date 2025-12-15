import os
import base64
import requests
from email.mime.text import MIMEText
from google.oauth2 import service_account
from googleapiclient.discovery import build

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
REAL_USER_EMAIL = "kadidia.sylla@mithrildefense.com"   # Workspace user with access to the mailbox
ATTIO_INBOUND = "mithrildefense@attio.email"      # Attio inbound address
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# ============= GMAIL SERVICE ============
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=REAL_USER_EMAIL
    )
    return build("gmail", "v1", credentials=creds)

# ============= FETCH MESSAGES ============
def fetch_messages(service, max_results=50):
    results = service.users().messages().list(userId="me", maxResults=max_results).execute()
    return results.get("messages", [])

# ============= PARSE EMAIL ============
def parse_message(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    
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
        "subject": headers.get("Subject", "(no subject)"),
        "from_email": headers.get("From", ""),
        "body": body
    }

# ============= POST TO ATTIO ============
def forward_to_attio(email):
    payload = {
        "to": ATTIO_INBOUND,
        "subject": email["subject"],
        "from": email["from_email"],
        "body": email["body"]
    }
    r = requests.post(f"https://api.attio.com/v2/forward", json=payload)
    r.raise_for_status()
    print("Forwarded to Attio:", email["subject"])

# ============= MAIN ============
def main():
    service = get_gmail_service()
    messages = fetch_messages(service)
    print(f"Found {len(messages)} messages")

    for m in messages:
        email = parse_message(service, m["id"])
        forward_to_attio(email)

if __name__ == "__main__":
    main()
