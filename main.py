import logging
import os
import base64
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ------------------ CONFIG ------------------
ATTIO_API_KEY = "ATTIO_TOKEN"
ATTIO_BASE_URL = "https://api.attio.com/v2"
GROUP_EMAIL = "kadidia.sylla@mithrildefense.com"  # Only process emails from this group
GMAIL_USER = "me"  # Gmail API 'me' refers to the authenticated user

HEADERS = {
    "Authorization": f"Bearer {ATTIO_API_KEY}",
    "Content-Type": "application/json"
}

# ------------------ LOGGING ------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ------------------ ATTIO HELPERS ------------------

def assert_person(name, email):
    payload = {"values": {"name": name, "primary_email_address": email}}
    try:
        r = requests.post(f"{ATTIO_BASE_URL}/objects/people/records/assert", headers=HEADERS, json=payload)
        r.raise_for_status()
        return r.json()  # contains 'record_id'
    except requests.HTTPError as e:
        logging.warning("Cannot assert person %s: %s", email, e.response.text)
        return None

def assert_company(domain):
    payload = {"values": {"name": domain, "domain": domain}}
    try:
        r = requests.post(f"{ATTIO_BASE_URL}/objects/companies/records/assert", headers=HEADERS, json=payload)
        r.raise_for_status()
        return r.json().get("record_id")
    except requests.HTTPError as e:
        logging.warning("Cannot assert company for domain %s: %s", domain, e.response.text)
        return None

def create_email_record(email_data, person_id, company_id=None):
    payload = {
        "values": {
            "subject": email_data["subject"],
            "from": email_data["from_email"],
            "body": email_data["body"],
            "to": email_data.get("to", []),
            "person": {"record_id": person_id}
        }
    }
    if company_id:
        payload["values"]["company"] = {"record_id": company_id}

    try:
        r = requests.post(f"{ATTIO_BASE_URL}/objects/emails/records", headers=HEADERS, json=payload)
        r.raise_for_status()
        logging.info("Created email record for %s", email_data["from_email"])
    except requests.HTTPError as e:
        logging.warning("Cannot create email record for %s: %s", email_data["from_email"], e.response.text)

# ------------------ GMAIL HELPERS ------------------

def get_gmail_service():
    """
    Uses a credentials JSON to authenticate Gmail API.
    Make sure you have 'token.json' with the Gmail OAuth2 credentials.
    """
    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/gmail.readonly"])
    service = build("gmail", "v1", credentials=creds)
    return service

def fetch_group_messages(service, max_results=50):
    """
    Fetches messages from Gmail API sent to the GROUP_EMAIL.
    Returns list of dicts: from_email, from_name, subject, body, message_id, to
    """
    query = f"to:{GROUP_EMAIL}"
    results = service.users().messages().list(userId=GMAIL_USER, q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        msg_detail = service.users().messages().get(userId=GMAIL_USER, id=msg["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in msg_detail["payload"]["headers"]}
        from_email = headers.get("From", "").split("<")[-1].strip(">")
        from_name = headers.get("From", "").split("<")[0].strip().strip('"')
        subject = headers.get("Subject", "")
        to_list = headers.get("To", "").split(",")

        # Extract body (supports plain text)
        body = ""
        if "parts" in msg_detail["payload"]:
            for part in msg_detail["payload"]["parts"]:
                if part.get("mimeType") == "text/plain":
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                    break
        else:
            body = base64.urlsafe_b64decode(msg_detail["payload"]["body"]["data"]).decode("utf-8")

        emails.append({
            "from_email": from_email,
            "from_name": from_name,
            "subject": subject,
            "body": body,
            "message_id": headers.get("Message-ID"),
            "to": to_list
        })
    return emails

# ------------------ SYNC LOOP ------------------

def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    emails = fetch_group_messages(service, max_results=max_results)
    logging.info("Found %d messages", len(emails))

    for email in emails:
        logging.info("Processing message ID: %s", email.get("message_id"))

        # Assert person
        person = assert_person(email["from_name"], email["from_email"])
        if not person:
            logging.warning("Skipping email — person unavailable: %s", email["from_email"])
            continue
        person_id = person.get("record_id")

        # Assert company from email domain
        domain = email["from_email"].split("@")[-1]
        company_id = assert_company(domain)

        # Create email record linked to person (and company)
        create_email_record(email, person_id, company_id)

# ------------------ MAIN ------------------

if __name__ == "__main__":
    sync_group_to_attio(max_results=100)
