# 🚨 IT Incident Auto-Responder

*A TiDB Serverless + AI Hackathon Project*

## 📌 Overview

The **IT Incident Auto-Responder** is an AI-powered assistant that automatically triages incidents, retrieves similar past cases, generates an action plan, and posts results to Slack — all backed by **TiDB Serverless with vector search**.

It’s designed to save engineers time during outages by turning noisy tickets or screenshots into **step-by-step resolutions** with confidence scoring, external notifications, and full audit trails.

---

## ✨ Features

* **📥 Ingestion Pipeline**

  * Loads synthetic tickets (`tickets.jsonl`) and manuals (PDFs) into TiDB.
  * Splits docs into chunks, embeds them with `text-embedding-3-small` (1536-dim), and stores vectors.

* **🔎 Hybrid Vector + Metadata Search**

  * Queries embeddings with **KNN cosine similarity**.
  * Supports structured filters: `service`, `error_code`, `env`, `keyword`.
  * Provides previews of similar past cases (context window).

* **🧠 Agentic LLM Planning**

  * Runs incidents through `retrieve_and_plan` → generates JSON with:

    * Action **steps**
    * Potential **risks**
    * Confidence score (0–1)
    * Source references (retrieved chunk IDs)
  * Categorizes severity & adds enrichment (system status check).

* **📲 Slack Integration**

  * Posts formatted plan summaries directly to a Slack channel.
  * Confidence thresholds control behavior:

    * `auto_fix`
    * `needs_human`
    * `discard`

* **🗂 Audit Trail (TiDB)**

  * Every run is persisted in the `runs` table.
  * Includes retrieved IDs, plan JSON, confidence, and action status.
  * Provides full replay capability with `/runs/{id}/replay`.

* **💻 Minimal Web UI (HTML + Tailwind + JS)**

  * Paste an incident → get structured plan and Slack notification.
  * Filter by service, error code, environment, or keyword.
  * View run history (in-progress: previews are logged but not always rendering).

---

## ⚡ Architecture

1. **Input**: Text incident or OCR-extracted image.
2. **Retrieve**: Hybrid vector + metadata search on TiDB.
3. **Plan**: LLM (OpenAI GPT-4o-mini) generates structured JSON plan.
4. **Notify**: Slack message posted with steps, risks, confidence.
5. **Persist**: Run saved into TiDB for audit trail & replay.

---

## 📂 Repository Structure

```
app/
 ├── agent.py        # retrieve_and_plan: builds plan JSON from LLM
 ├── server.py       # FastAPI app with /run, /ocr_run, /search, /runs
 ├── search.py       # hybrid_search: vector + filter retrieval from TiDB
 ├── db.py           # TiDB connection + helpers (insert, exec_sql)
 ├── tools/
 │    ├── slack.py   # Slack integration
 │    ├── jira.py    # (optional) Jira integration
 │    ├── ocr.py     # OCR support for screenshots
 │    └── enrich.py  # fetch_status enrichment
data/
 ├── manuals/        # Sample PDF manuals
 └── tickets.jsonl   # Synthetic tickets
static/
 └── index.html      # Minimal UI (Tailwind + JS)
```

---

## 🚀 Run Instructions

### 1. Environment

Copy `.env.example` → `.env` and fill in:

```
TIDB_HOST=...
TIDB_PORT=4000
TIDB_USER=...
TIDB_PASSWORD=...
TIDB_DB=incident_ai

OPENAI_API_KEY=sk-...

SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...

CONFIDENCE_MIN=0.65
CONFIDENCE_AUTO=0.80
```

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Ingest sample data

```bash
python app/ingest.py --tickets data/tickets.jsonl --pdf data/manuals
```

### 4. Run the server

```bash
python -m uvicorn app.server:app --reload
```

### 5. Try it out

* **Swagger UI** → [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **Run an incident**:

```json
POST /run
{
  "text": "AUTH-500 after login on prod",
  "top_k": 5,
  "filters": { "service": "auth", "error_code": "AUTH-500" },
  "post_to_slack": true
}
```

* Check Slack for the triage summary 🚀

---


## 📌 Notes & Known Issues

* **Context previews** (retrieved chunks) sometimes appear empty in UI due to vector parameter casting issues — but retrieval works internally (see `/run` response and Slack messages).
* The core pipeline (Ingest → Retrieve → Plan → Slack → Persist) is fully operational.
* Replay, OCR, and Jira are included but not the focus of the demo.

---

## 🏆 Why This Project

Incident response is stressful and time-critical. Our system:

* Reduces **mean time to resolution** by surfacing similar cases instantly.
* Produces **step-by-step playbooks** for engineers under pressure.
* Ensures **auditability** (every run logged in TiDB).
* Extends easily to other domains (customer support, supply chain, study assistant).

This is an **agentic workflow** that chains:

* Vector search (TiDB Serverless)
* LLM reasoning (GPT-4o)
* External action (Slack bot)

Exactly what the hackathon calls for ✅


## 📜 License

MIT
