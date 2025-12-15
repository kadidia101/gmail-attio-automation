import os
import base64
import requests
from email.utils import parseaddr
from googleapiclient.discovery import build
from google.oauth2 import service_account

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"  # service account JSON
WORKSPACE_USER_EMAIL = "kadidia.sylla@mithrildefense.com"  # impersonated user receiving Google Group emails

ATTIO_INBOUND_EMAIL = os.environ.get("ATTIO_INBOUND_EMAIL")  # e.g., yourworkspace@attio.email
ATTIO_HEADERS = {
    "Content-Type": "application/json",
}

# Gmail API readonly scope
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=WORKSPACE_USER_EMAIL  # impersonate workspace user
    )
    return build("gmail", "v1", credentials=creds)

# =======================
# FETCH MESSAGES
# =======================
def fetch_messages(service, max_results=50):
    """Fetch messages from the user's mailbox."""
    response = service.users().messages().list(
        userId="me",
        maxResults=max_results
    ).execute()
    messages = response.get("messages", [])
    return messages

# =======================
# PARSE EMAIL
# =======================
def parse_message(service, msg_id):
    """Return sender, subject, body, message_id."""
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full"
    ).execute()

    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    sender_name, sender_email = parseaddr(headers.get("From", ""))
    subject = headers.get("Subject", "")
    message_id = headers.get("Message-ID", "")

    body = ""
    payload = msg["payload"]
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
    elif "data" in payload.get("body", {}):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    return {
        "from_email": sender_email,
        "from_name": sender_name,
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }

# =======================
# FORWARD TO ATTIO
# =======================
def forward_to_attio(email):
    """Send the email content to Attio inbound email."""
    payload = {
        "from": email["from_email"],
        "to": ATTIO_INBOUND_EMAIL,
        "subject": email["subject"],
        "body": email["body"],
        "message_id": email["message_id"]
    }

    # Use SMTP, requests, or any library that allows sending raw email to Attio inbound
    # Here we use requests with Mailgun-like forwarding simulation:
    r = requests.post(f"https://api.mailgun.net/v3/fake-domain/messages", data=payload)  # placeholder
    r.raise_for_status()
    return r.json()

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio():
    service = get_gmail_service()
    messages = fetch_messages(service)
    print(f"Found {len(messages)} messages")

    if not messages:
        print("No messages to forward.")
        return

    for m in messages:
        email = parse_message(service, m["id"])
        if not email["message_id"]:
            print(f"Skipping message with no Message-ID: {email}")
            continue
        try:
            forward_to_attio(email)
            print(f"Forwarded: {email['from_email']} - {email['subject']}")
        except Exception as e:
            print(f"Failed to forward {email['from_email']}: {e}")

# =======================
# ENTRYPOINT
# =======================
if __name__ == "__main__":
    sync_group_to_attio()
