import os, requests

def fetch_status() -> dict | None:
    url = os.getenv("ENRICH_STATUS_ENDPOINT")
    if not url: return None
    try:
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            # keep it small
            return {"ok": True, "sample": str(data)[:400]}
        return {"ok": False, "code": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}
