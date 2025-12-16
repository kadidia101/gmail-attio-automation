import os
import time
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
GROUP_EMAIL = "kadidia.sylla@mithrildefense.com"
ATTIO_TOKEN = os.environ["ATTIO_TOKEN"]
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {
    "Authorization": f"Bearer {ATTIO_TOKEN}",
    "Content-Type": "application/json",
}
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# =======================
# GMAIL SERVICE
# =======================
def get_gmail_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
        subject=GROUP_EMAIL
    )
    return build("gmail", "v1", credentials=creds)

def fetch_messages(service, max_results=50):
    try:
        query = f"from:{GROUP_EMAIL}"
        res = service.users().messages().list(userId="me", maxResults=max_results, q=query).execute()
        return res.get("messages", [])
    except Exception as e:
        logging.error("Failed to fetch messages: %s", e)
        return []

def parse_message(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    sender_name, sender_email = parseaddr(headers.get("From", ""))
    subject = headers.get("Subject", "")
    message_id = headers.get("Message-ID")

    body = ""
    payload = msg.get("payload", {})
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
    elif "body" in payload and "data" in payload["body"]:
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    return {
        "from_email": sender_email.lower(),
        "from_name": sender_name,
        "subject": subject,
        "body": body,
        "message_id": message_id,
    }

def get_domain(email):
    return email.split("@")[-1].lower()

# =======================
# ATTIO HELPERS
# =======================
def find_company_by_domain(domain):
    """
    Search Attio companies for a given domain.
    Returns the record_id if found, else None.
    """
    search_url = f"{ATTIO_BASE}/objects/companies/records/search"
    payload = {
        "query": {
            "filters": [
                {
                    "attribute": "domains",
                    "operator": "contains",
                    "value": [{"domain": domain}]
                }
            ]
        }
    }

    resp = requests.post(search_url, headers=ATTIO_HEADERS, json=payload)
    if resp.status_code == 200:
        records = resp.json().get("records", [])
        if records:
            company = records[0]
            logging.info("Found existing company | domain=%s | record_id=%s", domain, company["record_id"])
            return company["record_id"]
    else:
        logging.warning("Failed to search company for domain %s: %s", domain, resp.text)
    return None

def create_company(domain):
    """
    Creates a new company with the given domain.
    Returns record_id if successful, else None.
    """
    payload = {
        "data": {
            "values": {
                "name": domain,
                "domains": [{"domain": domain}]
            }
        }
    }

    resp = requests.post(f"{ATTIO_BASE}/objects/companies/records", headers=ATTIO_HEADERS, json=payload)
    if resp.status_code in (200, 201):
        company = resp.json()
        logging.info("Created new company | domain=%s | record_id=%s", domain, company["record_id"])
        return company["record_id"]
    else:
        logging.warning("Failed to create company for domain %s: %s", domain, resp.text)
        return None

def get_or_create_company(domain):
    company_id = find_company_by_domain(domain)
    if company_id:
        return company_id
    return create_company(domain)

def assert_person_with_company(name, email):
    domain = get_domain(email)
    company_id = get_or_create_company(domain)
    if not company_id:
        logging.warning("Cannot assert person because company could not be found or created: %s", domain)
        return None

    # Split name
    if " " in name.strip():
        first_name, last_name = name.strip().split(" ", 1)
    else:
        first_name, last_name = name.strip(), ""

    payload = {
        "data": {
            "values": {
                "name": [{"first_name": first_name, "last_name": last_name, "full_name": name}],
                "email_addresses": [{"email_address": email}],
                "company": [{"target_object": "companies", "target_record_id": company_id}]
            }
        }
    }

    url = f"{ATTIO_BASE}/objects/people/records?matching_attribute=email_addresses"
    resp = requests.put(url, headers=ATTIO_HEADERS, json=payload)

    if resp.status_code in (200, 201):
        person = resp.json()
        logging.info("Asserted person | email=%s | record_id=%s | company_id=%s", email, person.get("record_id"), company_id)
        return person
    else:
        logging.warning("Failed to assert person %s: %s", email, resp.text)
        return None

def create_email_record(person_id, email):
    payload = {
        "values": {
            "subject": email["subject"],
            "body": email["body"],
            "from": email["from_email"],
            "external_message_id": email["message_id"],
            "person": [{"target_object": "people", "target_record_id": person_id}]
        }
    }
    r = requests.post(f"{ATTIO_BASE}/objects/emails/records", headers=ATTIO_HEADERS, json=payload)
    try:
        r.raise_for_status()
        logging.info("Synced email from %s", email["from_email"])
        return r.json()
    except requests.HTTPError as e:
        logging.warning("Cannot create email record for %s: %s", email["from_email"], e.response.text)
        return None

# =======================
# MAIN SYNC
# =======================
def sync_group_to_attio(max_results=50):
    service = get_gmail_service()
    messages = fetch_messages(service, max_results)
    logging.info("Found %d messages", len(messages))

    for m in messages:
        email = parse_message(service, m["id"])
        logging.info("Processing message ID: %s", email["message_id"])

        if GROUP_EMAIL not in email["from_email"]:
            logging.info("Skipping email not from group: %s", email["from_email"])
            continue
        if not email["message_id"]:
            logging.warning("Skipping message with no Message-ID: %s", email)
            continue

        domain = get_domain(email["from_email"])
        person = assert_person_with_company(email["from_name"], email["from_email"])

        if not person:
            logging.warning("Skipping email because person could not be asserted: %s", email["from_email"])
            continue

        create_email_record(person["record_id"], email)

# =======================
# ENTRYPOINT (5-min loop)
# =======================
if __name__ == "__main__":
    while True:
        try:
            logging.info("Starting Gmail → Attio sync...")
            sync_group_to_attio(max_results=100)
            logging.info("Sync complete. Waiting 5 minutes...")
        except Exception as e:
            logging.error("Error during sync: %s", e)
        time.sleep(300)  # 5 minutes

