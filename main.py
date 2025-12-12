import os
import base64
import json
import requests
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# =============================
# CONFIGURATION - fill these in
# =============================
CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

GMAIL_USER = "me"  # Gmail account receiving the Google Group emails

ATTIO_EMAIL_ENDPOINT = "https://api.attio.com/v2/objects/emails/records"  # Email Record endpoint
ATTIO_PERSON_ENDPOINT = "https://api.attio.com/v2/objects/persons/records"  # Person endpoint
ATTIO_PERSON_SEARCH = "https://api.attio.com/v2/objects/persons/records/search"  # Person search endpoint
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

headers = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json"
}

for msg in messages:
    message = service.users().messages().get(userId=GMAIL_USER, id=msg['id'], format='full').execute()

    # Extract headers
    headers_list = message['payload']['headers']
    subject = next((h['value'] for h in headers_list if h['name'] == 'Subject'), "")
    sender = next((h['value'] for h in headers_list if h['name'] == 'From'), "")

    # Extract body (plain text)
    if 'parts' in message['payload']:
        body_part = message['payload']['parts'][0]['body']
        body_data = body_part.get('data', '')
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')
    else:
        body_data = message['payload']['body'].get('data', '')
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')

    # =============================
    # FIND OR CREATE PERSON
    # =============================
    # Step 1: search for person
    search_payload = {
        "filter": {
            "attribute": "email_addresses",
            "operator": "contains",
            "value": sender
        }
    }
    resp = requests.post(ATTIO_PERSON_SEARCH, headers=headers, json=search_payload)
    resp_data = resp.json()
    if resp.status_code == 200 and resp_data["data"]:
        person_id = resp_data["data"][0]["id"]["record_id"]
    else:
        # Step 2: create new person
        create_payload = {
            "data": {
                "values": {
                    "email_addresses": [
                        {
                            "email_address": sender,
                            "original_email_address": sender,
                            "active_from": datetime.utcnow().isoformat() + "Z",
                            "attribute_type": "email-address"
                        }
                    ],
                    "name": sender  # fallback name field
                }
            }
        }
        resp = requests.post(ATTIO_PERSON_ENDPOINT, headers=headers, json=create_payload)
        if resp.status_code == 200:
            person_id = resp.json()["data"]["id"]["record_id"]
        else:
            print(f"Failed to create person for {sender}: {resp.text}")
            continue  # skip this email

    # =============================
    # CREATE EMAIL RECORD LINKED TO PERSON
    # =============================
    email_payload = {
        "data": {
            "values": {
                "title": subject,    # slug for Email Name field
                "urls": body,        # slug for URLs field
                "person": person_id  # record-reference field slug linking to the Person
            }
        }
    }

    resp = requests.post(ATTIO_EMAIL_ENDPOINT, headers=headers, json=email_payload)
    if resp.status_code == 200:
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
        print(f"Failed to send email {msg['id']} to Attio: {resp.status_code} {resp.text}")
