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

# Attio object slugs
PERSON_OBJECT_SLUG = "people"   # Your People object
EMAIL_OBJECT_SLUG = "emails"    # Your Email Record object
EMAIL_RECORD_PERSON_FIELD_SLUG = "person"  # Record-reference field on Email Record linking to Person

ATTIO_PERSON_SEARCH = f"https://api.attio.com/v2/objects/{PERSON_OBJECT_SLUG}/records"
ATTIO_EMAIL_ENDPOINT = f"https://api.attio.com/v2/objects/{EMAIL_OBJECT_SLUG}/records"

ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]

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
    # SEARCH FOR PERSON
    # =============================
    search_payload = {
        "data": {
            "filter": {
                "attribute": "email_addresses",
                "operator": "contains",
                "value": sender
            }
        }
    }

    resp = requests.post(ATTIO_PERSON_SEARCH, headers=headers, json=search_payload)
    if resp.status_code != 200:
        print(f"Failed to search person for {sender}: {resp.text}")
        continue

    resp_data = resp.json()
    if not resp_data.get("data"):
        print(f"No person found for email {sender}, skipping...")
        continue

    person_id_obj = resp_data["data"][0]["id"]  # contains workspace_id, object_id, record_id

    # =============================
    # CREATE EMAIL RECORD LINKED TO PERSON
    # =============================
    email_payload = {
        "data": {
            "values": {
                "title": subject,
                "urls": body,
                EMAIL_RECORD_PERSON_FIELD_SLUG: {"id": person_id_obj}  # record-reference
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
