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
GROUP_EMAIL = "automation@mithrildefense.com"  # Google Group mailbox
ATTIO_INBOUND_EMAIL = "mithrildefense@attio.email"  # Your Attio inbound email

ATTIO_API_KEY = os.environ.get("ATTIO_TOKEN")  # optional if needed for API
ATTIO_HEADERS = {"Authorization": f"Bearer {ATTIO_API_KEY}"} if ATTIO_API_KEY else {}

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GROUP_EMAIL  # impersonate the Google Group
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
    message_id = headers.get("Message-ID", "")
    
    # Decode body
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
        "message_id": message_id
    }

# =======================
# FORWARD TO ATTIO
# =======================
def forward_to_attio(email):
    # Build the email content
    content = f"From: {email['from_name']} <{email['from_email']}>\nSubject: {email['subject']}\n\n{email['body']}"
    
    # Send via POST to Attio inbound email (using requests + SMTP headers if needed)
    r = requests.post(
        f"https://api.mailgun.net/v3/fake-domain/messages",  # Replace with Attio inbound email method
        auth=("api", ATTIO_API_KEY) if ATTIO_API_KEY else None,
        data={
            "from": email['from_email'],
            "to": ATTIO_INBOUND_EMAIL,
            "subject": email['subject'],
            "text": email['body']
        }
    )
    r.raise_for_status()
    print(f"Forwarded: {email['from_email']}")

# =======================
# MAIN SYNC
# =======================
def main():
    service = get_gmail_service()
    messages = fetch_messages(service)
    print(f"Found {len(messages)} messages")
    
    for m in messages:
        email = parse_message(service, m["id"])
        if not email["message_id"]:
            print(f"Skipping message with no Message-ID: {email}")
            continue
        try:
            forward_to_attio(email)
        except Exception as e:
            print(f"Failed to forward {email['from_email']}: {e}")

if __name__ == "__main__":
    main()
