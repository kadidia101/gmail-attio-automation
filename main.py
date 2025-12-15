import os
import base64
import logging
from email.utils import parseaddr
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

# =======================
# CONFIG
# =======================
SERVICE_ACCOUNT_FILE = "/home/ec2-user/.secrets/service_account.json"
GROUP_EMAIL = "automation@mithrildefense.com"  # Google Group email
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
IMPERSONATE_USER = "kadidia.sylla@mithrildefense.com"  # Inbox that receives all group emails

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=IMPERSONATE_USER  # impersonate real user
    )
    service = build("gmail", "v1", credentials=creds)
    return service

def fetch_messages(service, max_results=50):
    """Fetch messages from the mailbox, only sent to GROUP_EMAIL."""
    try:
        query = f"to:{GROUP_EMAIL}"
        res = service.users().messages().list(userId="me", maxResults=max_results, q=query).execute()
        return res.get("messages", [])
    except Exception as e:
        logging.error("Failed to fetch messages: %s", e)
        return []

def parse_message(service, msg_id):
    """Parse Gmail message and extract essential info."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    
    # Extract headers
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    subject = headers.get("subject", "")
    
    # Gmail header for Message-ID can vary in case
    message_id = headers.get("message-id") or headers.get("Message-ID")
    
    # Extract body
    def get_body(payload):
        if "parts" in payload:
            for part in payload["parts"]:
                # Handle nested multipart
                if part.get("mimeType") == "multipart/alternative":
                    body = get_body(part)
                    if body:
                        return body
                elif part.get("mimeType") in ("text/plain", "text/html") and "data" in part.get("body", {}):
                    data = part["body"]["data"]
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif "body" in payload and "data" in payload["body"]:
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        return ""
    
    body = get_body(msg.get("payload", {}))
    
    return {
        "from_email": sender_email,
        "from_name": sender_name,
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }

# =======================
# ATTIO HELPERS
# =======================
def email_already_ingested(message_id):
    if not message_id:
        return False
    r = requests.post(
        f"{ATTIO_BASE}/objects/emails/records/query",
        headers=ATTIO_HEADERS,
        json={"filter": {"external_message_id": {"equals": message_id}}},
    )
    return len(r.json().get("records", [])) > 0

def find_person_by_email(email):
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records/query",
            headers=ATTIO_HEADERS,
            json={"filter": {"primary_email_address": {"equals": email}}, "limit": 1},
        )
        r.raise_for_status()
        records = r.json().get("records", [])
        return records[0] if records else None
    except requests.HTTPError as e:
        logging.error("Attio person query failed | email=%s | %s", email, e)
        return None

def get_or_create_person(email, name):
    # Step 1: Try to fetch existing person
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records/query",
            headers=ATTIO_HEADERS,
            json={"filter": {"primary_email_address": {"equals": email}}, "limit": 1},
        )
        r.raise_for_status()
        records = r.json().get("records", [])
        if records:
            return records[0]  # return existing person
    except requests.HTTPError as e:
        logging.error("Attio person query failed | email=%s | %s", email, e)

    # Step 2: Attempt to create
    payload = {"values": {"name": name or email, "primary_email_address": email}}
    try:
        r = requests.post(f"{ATTIO_BASE}/objects/people/records", headers=ATTIO_HEADERS, json=payload)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if "uniqueness_conflict" in str(e):
            # If uniqueness conflict, refetch
            logging.info("Uniqueness conflict, refetching existing person: %s", email)
            try:
                r = requests.post(
                    f"{ATTIO_BASE}/objects/people/records/query",
                    headers=ATTIO_HEADERS,
                    json={"filter": {"primary_email_address": {"equals": email}}, "limit": 1},
                )
                r.raise_for_status()
                records = r.json().get("records", [])
                if records:
                    return records[0]
            except requests.HTTPError as e2:
                logging.error("Attio refetch failed | email=%s | %s", email, e2)
        logging.warning("Cannot create person %s: %s", email, e)
        return None

def find_company_by_domain(domain):
    r = requests.post(
        f"{ATTIO_BASE}/objects/companies/records/query",
        headers=ATTIO_HEADERS,
        json={"filter": {"domain": {"equals": domain}}},
    )
    records = r.json().get("records", [])
    return records[0] if records else None

def create_company(domain):
    payload = {"values": {"name": domain, "domain": domain}}
    try:
        r = requests.post(f"{ATTIO_BASE}/objects/companies/records", headers=ATTIO_HEADERS, json=payload)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        logging.warning("Cannot create company for domain %s: %s", domain, e)
        return None

def create_email_record(person_id, email):
    payload = {
        "data": {
            "values": {
                "subject": email["subject"],
                "body": email["body"],
                "external_message_id": email["message_id"],
                "participants": [
                    {
                        "role": "from",
                        "person": {
                            "target_object": "people",
                            "target_record_id": person_id
                        }
                    }
                ]
            }
        }
    }

    r = requests.post(
        f"{ATTIO_BASE}/objects/emails/records",
        headers=ATTIO_HEADERS,
        json=payload,
    )

    if r.status_code not in (200, 201):
        logging.warning(
            "Cannot create email record | status=%s | response=%s",
            r.status_code,
            r.text,
        )
        return None

    return r.json()

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    messages = fetch_messages(service, max_results)
    logging.info("Found %d messages", len(messages))

    for m in messages:
        email = parse_message(service, m["id"])
        # Filter only emails coming from the group (optional if inbox is dedicated)
        #if GROUP_EMAIL not in email["body"] and email["from_email"] != GROUP_EMAIL:
        #    logging.info("Skipping email not from group: %s", email["from_email"])
        #    continue

        # Print message ID
        logging.info("Processing message ID: %s", email.get("message_id"))

        # Skip if no Message-ID
        if not email.get("message_id"):
            logging.warning("Skipping message with no Message-ID: %s", email)
            continue

        # Skip duplicates
        if email_already_ingested(email["message_id"]):
            logging.info("Skipping duplicate message: %s", email["message_id"])
            continue
 
       # ---------- PERSON HANDLING ----------
        person = get_or_create_person(email["from_email"], email["from_name"])
        if not person:
            logging.warning("Skipping email — person unavailable: %s", email["from_email"])
            continue
        
        # Extract the Attio record ID safely
        person_id = person.get("record_id")  # usually record_id in Attio API
        if not person_id:
            logging.warning("Skipping email — person record_id missing: %s", email["from_email"])
            continue

        # ---------- COMPANY HANDLING ----------
        domain = email["from_email"].split("@")[-1]
        company = find_company_by_domain(domain)
        if not company:
            company = create_company(domain)
            if not company:
                logging.warning("Workspace settings prevent creating company for domain: %s", domain)

        # ---------- CREATE EMAIL RECORD ----------
        create_email_record(person_id, email, company_id=company["id"] if company else None)
        logging.info("Synced email from %s", email["from_email"])

# =======================
# ENTRYPOINT
# =======================
if __name__ == "__main__":
    sync_group_to_attio(max_results=100)
