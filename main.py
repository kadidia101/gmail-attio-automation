import os
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import email
import time

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GMAIL_USER = "central-mailbox@company.com"  # Workspace user that receives Google Group emails
ATTIO_INBOUND_EMAIL = "workspace@attio.email"  # Attio inbound email

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

FORWARDED_LABEL = "Forwarded_to_Attio"  # Gmail label to mark processed emails

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GMAIL_USER  # impersonate the mailbox
    )
    return build("gmail", "v1", credentials=creds)

# =======================
# FETCH MESSAGES
# =======================
def fetch_unprocessed_messages(service, label_ids=None, max_results=50):
    label_ids = label_ids or []
    try:
        response = service.users().messages().list(
            userId="me",
            labelIds=label_ids,
            maxResults=max_results
        ).execute()
        return response.get("messages", [])
    except HttpError as error:
        print("An error occurred fetching messages:", error)
        return []

# =======================
# GET FULL MESSAGE
# =======================
def get_message(service, msg_id):
    return service.users().messages().get(
        userId="me", id=msg_id, format="raw"
    ).execute()

# =======================
# FORWARD TO ATTIO
# =======================
def forward_message(service, msg):
    try:
        raw_msg_bytes = base64.urlsafe_b64decode(msg['raw'])
        # Create a new message object with the original content
        original_msg = email.message_from_bytes(raw_msg_bytes)
        
        # Set new headers for forwarding
        original_msg.replace_header('To', ATTIO_INBOUND_EMAIL)
        
        # Encode back to base64
        raw_forward = base64.urlsafe_b64encode(original_msg.as_bytes()).decode()
        
        # Send
        message = {'raw': raw_forward}
        sent = service.users().messages().send(userId="me", body=message).execute()
        return sent
    except Exception as e:
        print("Failed to forward:", e)
        return None

# =======================
# APPLY LABEL
# =======================
def apply_label(service, msg_id, label_name):
    try:
        # Get or create label
        labels = service.users().labels().list(userId="me").execute().get('labels', [])
        label_id = None
        for lbl in labels:
            if lbl['name'] == label_name:
                label_id = lbl['id']
                break
        if not label_id:
            label = service.users().labels().create(
                userId="me",
                body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
            ).execute()
            label_id = label['id']
        # Apply label
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [label_id]}
        ).execute()
    except Exception as e:
        print("Failed to apply label:", e)

# =======================
# MAIN SYNC LOOP
# =======================
def main():
    service = get_gmail_service()
    messages = fetch_unprocessed_messages(service)
    print(f"Found {len(messages)} messages to process.")

    for msg_ref in messages:
        msg = get_message(service, msg_ref['id'])
        sent = forward_message(service, msg)
        if sent:
            apply_label(service, msg_ref['id'], FORWARDED_LABEL)
            print(f"Forwarded message {msg_ref['id']} to Attio.")

if __name__ == "__main__":
    main()
