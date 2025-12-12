import os
import base64
import json
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# =============================
# CONFIGURATION - fill these in
# =============================
CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

GMAIL_USER = "me"  # Gmail account receiving the Google Group emails

ATTIO_ENDPOINT = "https://api.attio.com/v2/objects/emails/records"  # Attio endpoint URL
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]  # Attio API key / permanent token

# Optional: label to mark processed emails
PROCESSED_LABEL = "Processed"

# =============================
# AUTHENTICATE WITH GMAIL
# =============================
creds = Credentials(
    None,
    refresh_token=REFRESH_TOKEN,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    token_uri="https://oauth2.googleapis.com/token"
)

service = build('gmail', 'v1', credentials=creds)

# =============================
# FETCH UNREAD EMAILS
# =============================
results = service.users().messages().list(userId=GMAIL_USER, labelIds=['INBOX'], q="is:unread").execute()
messages = results.get('messages', [])

if not messages:
    print("No new messages.")
else:
    print(f"Found {len(messages)} unread messages.")

for msg in messages:
    message = service.users().messages().get(userId=GMAIL_USER, id=msg['id'], format='full').execute()

    # Extract headers
    headers = message['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "")
    sender = next((h['value'] for h in headers if h['name'] == 'From'), "")

    # Extract body (plain text)
    if 'parts' in message['payload']:
        body_part = message['payload']['parts'][0]['body']
        body_data = body_part.get('data', '')
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')
    else:
        body_data = message['payload']['body'].get('data', '')
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')

    # =============================
    # SEND TO ATTIO
    # =============================
    payload = {
        "data": {
            "from": sender,
            "subject": subject,
            "body": body
        }
    }
    headers = {
        "Authorization": f"Bearer {ATTIO_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(ATTIO_ENDPOINT, json=payload, headers=headers)

    if response.status_code == 200:
        print(f"Sent email {msg['id']} to Attio successfully.")

        # MARK AS PROCESSED
        # Create the label if it does not exist
        labels = service.users().labels().list(userId=GMAIL_USER).execute().get('labels', [])
        if not any(l['name'] == PROCESSED_LABEL for l in labels):
            service.users().labels().create(userId=GMAIL_USER, body={'name': PROCESSED_LABEL}).execute()

        service.users().messages().modify(
            userId=GMAIL_USER,
            id=msg['id'],
            body={'removeLabelIds': ['UNREAD'], 'addLabelIds': [PROCESSED_LABEL]}
        ).execute()
    else:
        print(f"Failed to send email {msg['id']} to Attio: {response.status_code} {response.text}")
