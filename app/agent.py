import os, sys, json
from dotenv import load_dotenv
from openai import OpenAI
from app.search import hybrid_search  # use hybrid, falls back to pure vector logic inside

load_dotenv()
client = OpenAI()
MODEL = os.getenv("PLAN_MODEL", "gpt-4o-mini")  # set PLAN_MODEL in .env if you want

SYSTEM = (
    "You are an incident fixer. Output ONLY compact JSON with keys: "
    "plan.steps[] as strings, plan.risks[] as strings, confidence float 0-1, sources[] chunk_ids. "
    "Keep steps actionable. No prose, no markdown, only JSON."
)

USER_TMPL = """Incident:
{incident}

Context (top hits):
{ctx}

Return JSON only.
"""

def retrieve_and_plan(incident_text: str, top_k: int = 5, filters: dict | None = None):
    # 1) retrieve
    hits = hybrid_search(incident_text, top_k=top_k, filters=filters)

    # 2) build compact context
    ctx_lines, src_ids = [], []
    for h in hits:
        src_ids.append(h["chunk_id"])
        meta = h.get("metadata") or {}
        line = (
            f"[id:{h['chunk_id']}] "
            f"[svc:{meta.get('service','')}] "
            f"[code:{meta.get('error_code','')}] "
            f"{(h['text'] or '')[:400]}"
        )
        ctx_lines.append(line)

    prompt = USER_TMPL.format(incident=incident_text, ctx="\n".join(ctx_lines))

    # 3) LLM plan
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content
    try:
        js = json.loads(raw)
    except Exception:
        js = {"plan": {"steps": ["Review top results and apply known fix"], "risks": []},
              "confidence": 0.5, "sources": src_ids[:top_k]}

    # make sure sources exist
    if "sources" not in js or not js["sources"]:
        js["sources"] = src_ids[:top_k]

    # 4) flatten to a single dict for Slack and DB
    return {
        "steps": (js.get("plan") or {}).get("steps", []),
        "risks": (js.get("plan") or {}).get("risks", []),
        "confidence": js.get("confidence", 0.0),
        "sources": js.get("sources", src_ids[:top_k]),
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m app.agent "your incident text"')
        sys.exit(1)
    query = sys.argv[1]
    out = retrieve_and_plan(query, top_k=5)
    print(json.dumps(out, indent=2))

# app/agent.py

def categorize_incident(incident_text: str, previews: list[dict]) -> dict:
    """Return JSON like {"category": "...", "severity": "low|medium|high", "tags": []}"""
    ctx = "\n".join(
        f"[{p.get('service','')}/{p.get('error_code','')}] {p.get('snippet','')[:120]}"
        for p in (previews or [])[:3]
    )
    prompt = (
        "Classify incident. Return JSON with keys: category, severity one of [low, medium, high], tags[].\n"
        f"Incident: {incident_text}\nContext:\n{ctx}\nReturn JSON only."
    )
    resp = client.chat.completions.create(
        model=os.getenv("PLAN_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system", "content": "Return JSON only"},
                  {"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {"category": "general", "severity": "medium", "tags": []}
