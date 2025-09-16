# Standard library
import os
import shutil
import tempfile
import json
from typing import Optional, Dict, Any

# Third-party
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

#
from app.agent import retrieve_and_plan, categorize_incident
from app.search import hybrid_search
from app.tools.jira import create_ticket
from app.tools.ocr import ocr_image
from app.tools.slack import post_plan, SLACK_CHANNEL_ID
from app.db import insert_incident, insert_run, exec_sql
from app.tools.enrich import fetch_status

load_dotenv()
app = FastAPI(title="Incident Auto-Responder API")
app.mount("/static", StaticFiles(directory="static"), name="static")

CONF_MIN = float(os.getenv("CONFIDENCE_MIN", "0.65"))
CONF_AUTO = float(os.getenv("CONFIDENCE_AUTO", "0.80"))



def _as_json(val):
    """Parse JSON strings (from DB) into Python; leave others as-is."""
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", errors="ignore")
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _clean_filters(src: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Keep only known keys and drop empty strings/None."""
    if not src:
        return {}
    out: Dict[str, str] = {}
    for k in ("service", "error_code", "env", "keyword"):
        v = src.get(k)
        if isinstance(v, str):
            v = v.strip()
        if v:
            out[k] = v
    return out


def _row_to_run(r):
    """Normalize DB row → run dict (supports tuple or dict rows)."""
    if isinstance(r, dict):
        rid = r.get("id")
        iid = r.get("incident_id")
        retrieved = _as_json(r.get("retrieved_ids"))
        plan = _as_json(r.get("plan"))
        action = r.get("action_status")
        created = r.get("created_at")
    else:
        # tuple order: id, incident_id, retrieved_ids, plan, action_status, created_at
        rid, iid, retrieved, plan, action, created = r
        retrieved = _as_json(retrieved)
        plan = _as_json(plan)
    return {
        "run_id": rid,
        "incident_id": iid,
        "retrieved_ids": retrieved,
        "plan": plan,
        "action_status": action,
        "created_at": created,
        "confidence": (plan or {}).get("confidence", 0.0),
        "next_action": (plan or {}).get("next_action"),
        "previews": (plan or {}).get("previews", []),
        "slack_ts": (plan or {}).get("slack_ts"),  # tolerate missing
    }


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class RunReq(BaseModel):
    text: str
    top_k: int = 5
    filters: Optional[Dict[str, Any]] = None  # service, error_code, env, keyword
    post_to_slack: bool = True
    create_jira: bool = False


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------
@app.post("/search")
def api_search(req: RunReq):
    hits = hybrid_search(req.text, top_k=req.top_k, filters=_clean_filters(req.filters))
    return {"count": len(hits), "results": hits}


@app.post("/run")
def api_run(req: RunReq):
    """Primary endpoint: retrieve → plan → (optional) notify → persist."""
    # 1) persist incident shell (raw text only for now)
    incident_id = insert_incident(req.text, "")

    # 2) retrieval with cleaned filters (used for both retrieval *and* planning)
    flt = _clean_filters(req.filters)
    hits = hybrid_search(req.text, top_k=req.top_k, filters=flt)
    retrieved_ids = [h["chunk_id"] for h in hits]

    # Short previews for UI/Slack
    previews = [
        {
            "id": h["chunk_id"],
            "service": (h.get("metadata") or {}).get("service", ""),
            "error_code": (h.get("metadata") or {}).get("error_code", ""),
            "snippet": (h.get("text") or "")[:180],
        }
        for h in hits
    ]

    # 3) plan (FIX #1: pass the *cleaned* filters so plan matches retrieval)
    plan = retrieve_and_plan(req.text, top_k=req.top_k, filters=flt)
    conf = float(plan.get("confidence", 0.0))

    # classify & enrich
    cats = categorize_incident(req.text, previews)
    plan["category"] = cats.get("category")
    plan["severity"] = cats.get("severity")
    plan["tags"] = cats.get("tags", [])

    enrich = fetch_status()
    if enrich:
        plan["enrichment"] = enrich

    # Confidence → next_action
    next_action = (
        "auto_fix"
        if conf >= CONF_AUTO
        else ("needs_human" if conf >= CONF_MIN else "discard")
    )
    plan["next_action"] = next_action
    needs_approval = conf < CONF_MIN

    # include previews for clients/Slack
    plan["previews"] = previews

    # 4) Slack (optional)
    action_status = "skipped_low_conf"
    slack_ts = None
    if req.post_to_slack and conf >= CONF_MIN:
        slack_ts = post_plan(SLACK_CHANNEL_ID, req.text, plan)
        action_status = "posted" if slack_ts else "post_failed"

    # 5) Optional Jira (only when confident)
    jira = None
    if req.create_jira and conf >= CONF_MIN:
        summary = f"[Auto] {req.text[:90]}"
        body = "Steps:\n- " + "\n- ".join(plan.get("steps", [])[:6])
        jira = create_ticket(summary, body)
        action_status = action_status + ("; jira_ok" if jira.get("ok") else "; jira_skip")

    # 6) persist run
    final_summary = (
        f"Incident triaged. Action: {plan.get('next_action','needs_human')} "
        f"Conf: {conf:.2f}. Top step: {(plan.get('steps') or [''])[0]}"
    )

    run_id = insert_run(
        incident_id=incident_id,
        retrieved_ids=retrieved_ids,
        plan_json=plan,
        final_summary=final_summary,
        action_status=action_status,
    )

    return {
        "incident_id": incident_id,
        "run_id": run_id,
        "confidence": conf,
        "next_action": next_action,  # included for UI
        "needs_approval": needs_approval,
        "plan": plan,
        "retrieved_ids": retrieved_ids,
        "previews": previews,
        "slack_ts": slack_ts,
        "action_status": action_status,
        "jira": jira,
    }


@app.post("/ocr_run")
def ocr_run(
    file: UploadFile = File(...),
    top_k: int = Form(5),
    post_to_slack: bool = Form(True),
    service: Optional[str] = Form(None),
    error_code: Optional[str] = Form(None),
    env: Optional[str] = Form(None),
    keyword: Optional[str] = Form(None),
):
    """OCR an uploaded image, retrieve context, plan, and optionally notify."""
    # 1) save upload to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename or "")[1]) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        extracted = ocr_image(tmp_path)
        if not extracted:
            return {"error": "ocr_empty", "message": "No text detected in the image."}

        incident_id = insert_incident(raw_input=f"image:{file.filename}", extracted_text=extracted)

        # Clean filters (from form fields)
        flt = _clean_filters({
            "service": service,
            "error_code": error_code,
            "env": env,
            "keyword": keyword,
        })

        # 2) single retrieval call (FIX #2)
        hits = hybrid_search(extracted, top_k=top_k, filters=flt)
        previews = [
            {
                "id": h["chunk_id"],
                "service": (h.get("metadata") or {}).get("service", ""),
                "error_code": (h.get("metadata") or {}).get("error_code", ""),
                "snippet": (h.get("text") or "")[:180],
            }
            for h in hits
        ]
        retrieved_ids = [h["chunk_id"] for h in hits]

        # 3) plan (keep consistent with retrieval filters for coherence)
        plan = retrieve_and_plan(extracted, top_k=top_k, filters=flt)
        conf = float(plan.get("confidence", 0.0))

        cats = categorize_incident(extracted, previews)
        plan["category"] = cats.get("category")
        plan["severity"] = cats.get("severity")
        plan["tags"] = cats.get("tags", [])

        enrich = fetch_status()
        if enrich:
            plan["enrichment"] = enrich

        next_action = (
            "auto_fix"
            if conf >= CONF_AUTO
            else ("needs_human" if conf >= CONF_MIN else "discard")
        )
        plan["next_action"] = next_action
        needs_approval = conf < CONF_MIN
        plan["previews"] = previews

        # 4) Slack (optional)
        action_status = "skipped_low_conf"
        slack_ts = None
        if post_to_slack and conf >= CONF_MIN:
            slack_ts = post_plan(SLACK_CHANNEL_ID, f"OCR incident: {file.filename}", plan)
            action_status = "posted" if slack_ts else "post_failed"

        # 5) persist
        final_summary = (
            f"OCR triage. Conf: {conf:.2f}. Top step: {(plan.get('steps') or [''])[0]}"
        )
        run_id = insert_run(
            incident_id=incident_id,
            retrieved_ids=retrieved_ids,
            plan_json=plan,
            final_summary=final_summary,
            action_status=action_status,
        )

        return {
            "incident_id": incident_id,
            "run_id": run_id,
            "confidence": conf,
            "needs_approval": needs_approval,
            "plan": plan,
            "retrieved_ids": retrieved_ids,
            "previews": previews,
            "slack_ts": slack_ts,
            "action_status": action_status,
        }
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@app.get("/runs")
def list_runs(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    incident_id: Optional[int] = None,
):
    """Return recent runs (most-recent first). Optional filter: incident_id."""
    if incident_id is not None:
        rows = exec_sql(
            """
            SELECT id, incident_id, retrieved_ids, plan, action_status, created_at
            FROM runs
            WHERE incident_id=%s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (incident_id, limit, offset),
        )
    else:
        rows = exec_sql(
            """
            SELECT id, incident_id, retrieved_ids, plan, action_status, created_at
            FROM runs
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )

    rows = rows or []
    return [_row_to_run(r) for r in rows]


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    rows = exec_sql(
        """
        SELECT id, incident_id, retrieved_ids, plan, action_status, created_at
        FROM runs
        WHERE id=%s
        LIMIT 1
        """,
        (run_id,),
    )
    if not rows:
        return {"error": "not_found", "run_id": run_id}

    r = rows[0]
    return {
        "run_id": r[0],
        "incident_id": r[1],
        "retrieved_ids": _as_json(r[2]),
        "plan": _as_json(r[3]),
        "action_status": r[4],
        "created_at": r[5],
    }


@app.post("/runs/{run_id}/replay")
def replay_run(run_id: int, post_to_slack: bool = True):
    """Re-run planning/retrieval for an existing incident and store as a new run."""
    # Fetch the original incident text and retrieved_ids
    row = exec_sql(
        "SELECT i.raw_input, i.extracted_text, r.retrieved_ids "
        "FROM runs r JOIN incidents i ON r.incident_id = i.id WHERE r.id=%s LIMIT 1",
        (run_id,),
    )
    if not row:
        return {"error": "not_found", "run_id": run_id}

    raw_input, extracted_text, _old_retrieved = row[0]
    text = (extracted_text or "").strip() or (raw_input or "")

    # Fresh retrieval and plan (no filters here by design)
    hits = hybrid_search(text, top_k=5)
    previews = [
        {
            "id": h["chunk_id"],
            "service": (h.get("metadata") or {}).get("service", ""),
            "error_code": (h.get("metadata") or {}).get("error_code", ""),
            "snippet": (h.get("text") or "")[:180],
        }
        for h in hits
    ]

    plan = retrieve_and_plan(text, top_k=5)
    conf = float(plan.get("confidence", 0.0))
    plan["previews"] = previews

    enrich = fetch_status()
    if enrich:
        plan["enrichment"] = enrich

    next_action = (
        "auto_fix"
        if conf >= CONF_AUTO
        else ("needs_human" if conf >= CONF_MIN else "discard")
    )
    plan["next_action"] = next_action

    # Slack (optional)
    action_status = "skipped_low_conf"
    slack_ts = None
    if post_to_slack and conf >= CONF_MIN:
        slack_ts = post_plan(SLACK_CHANNEL_ID, f"Replayed incident from run {run_id}", plan)
        action_status = "posted" if slack_ts else "post_failed"

    # FIX #3: Use safe join for final_summary; ensure no legacy summary_line usage
    incident_id = exec_sql("SELECT incident_id FROM runs WHERE id=%s", (run_id,))[0][0]
    new_run_id = insert_run(
        incident_id=incident_id,
        retrieved_ids=[h["chunk_id"] for h in hits],
        plan_json=plan,
        final_summary="; ".join(plan.get("steps", [])[:3]),
        action_status=action_status,
    )

    return {
        "replayed_from": run_id,
        "run_id": new_run_id,
        "confidence": conf,
        "plan": plan,
        "slack_ts": slack_ts,
        "action_status": action_status,
    }


@app.get("/health")
def health():
    return {
        "conf_min": CONF_MIN,
        "conf_auto": CONF_AUTO,
    }
