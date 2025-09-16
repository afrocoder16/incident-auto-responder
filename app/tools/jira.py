import os, base64, requests
from dotenv import load_dotenv
load_dotenv()

JIRA_BASE = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT = os.getenv("JIRA_PROJECT_KEY", "")

def _auth_header():
    token = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

def create_ticket(summary: str, description: str):
    # If not configured, return a harmless status so the app doesn’t crash
    if not (JIRA_BASE and JIRA_EMAIL and JIRA_TOKEN and JIRA_PROJECT):
        return {"ok": False, "error": "jira_not_configured"}
    url = f"{JIRA_BASE}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT},
            "summary": summary,
            "issuetype": {"name": "Task"},
            "description": description
        }
    }
    r = requests.post(url, headers=_auth_header(), json=payload, timeout=20)
    if r.status_code in (200, 201):
        data = r.json()
        return {"ok": True, "key": data.get("key"), "id": data.get("id")}
    return {"ok": False, "error": f"{r.status_code}:{r.text}"}
