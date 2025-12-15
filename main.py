import os
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
from email.mime.text import MIMEText

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GROUP_EMAIL = "automation@mithrildefense.com"          # Google Group address
ATTIO_INBOUND_EMAIL = "mithrildefense@attio.email"

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject="kadidia.sylla@mithrildefense.com"  # impersonate the group mailbox
    )
    return build("gmail", "v1", credentials=creds)

# =======================
# FETCH MESSAGES
# =======================
def fetch_messages(service, max_results=50):
    response = service.users().messages().list(
        userId="me",
        maxResults=max_results
    ).execute()
    return response.get("messages", [])

# =======================
# FORWARD MESSAGE
# =======================
def forward_message(service, msg_id, to_email):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    
    # Extract body
    payload = msg["payload"]
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and "data" in part["body"]:
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
    elif "data" in payload["body"]:
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    # Compose forward email
    subject = next((h["value"] for h in payload["headers"] if h["name"] == "Subject"), "(No Subject)")
    mime_msg = MIMEText(body, _charset="utf-8")
    mime_msg["To"] = to_email
    mime_msg["Subject"] = f"FWD: {subject}"

    raw_msg = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio():
    service = get_gmail_service()
    messages = fetch_messages(service)
    if not messages:
        print("No messages to forward.")
        return
    
    print(f"Found {len(messages)} messages. Forwarding to Attio...")
    for m in messages:
        try:
            forward_message(service, m["id"], ATTIO_INBOUND_EMAIL)
            print(f"Forwarded message {m['id']} to {ATTIO_INBOUND_EMAIL}")
        except Exception as e:
            print(f"Failed to forward message {m['id']}: {e}")

if __name__ == "__main__":
    sync_group_to_attio()
