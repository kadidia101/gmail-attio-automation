import os
import base64
import json
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# =============================
# CONFIGURATION
# =============================
CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

GMAIL_USER = "me"

ATTIO_EMAIL_ENDPOINT = "https://api.attio.com/v2/objects/emails/records"
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]

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
results = service.users().messages().list(
    userId=GMAIL_USER, labelIds=['INBOX'], q="is:unread"
).execute()
messages = results.get('messages', [])

if not messages:
    print("No new messages.")
else:
    print(f"Found {len(messages)} unread messages.")

headers = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json"
}

for msg in messages:
    message = service.users().messages().get(userId=GMAIL_USER, id=msg['id'], format='full').execute()

    # Extract headers
    headers_list = message['payload']['headers']
    subject = next((h['value'] for h in headers_list if h['name'] == 'Subject'), "")
    
    # Extract body (plain text)
    if 'parts' in message['payload']:
        body_part = message['payload']['parts'][0]['body']
        body_data = body_part.get('data', '')
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')
    else:
        body_data = message['payload']['body'].get('data', '')
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')

    # =============================
    # CREATE EMAIL RECORD IN ATTIO
    # =============================
    payload = {
        "data": {
            "values": {
                "email": subject,
                "urls": body
            }
        }
    }

    response = requests.post(ATTIO_EMAIL_ENDPOINT, headers=headers, json=payload)

    if response.status_code == 200:
        print(f"Sent email {msg['id']} to Attio successfully.")

        # MARK AS PROCESSED
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
