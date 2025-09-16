import os
import json
from pathlib import Path
from typing import Iterable, Tuple, List

from dotenv import load_dotenv
from pypdf import PdfReader
from openai import OpenAI

from app.db import exec_sql, query_all

load_dotenv()
client = OpenAI()

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))

# ---------- helpers

def clean_text(s: str) -> str:
    return " ".join(s.split())

def chunk_text(text: str, max_chars: int = 1200) -> Iterable[str]:
    text = clean_text(text)
    for i in range(0, len(text), max_chars):
        yield text[i:i+max_chars]

def ensure_tables_exist_note():
    # Optional sanity reminder if someone forgot to run schema
    pass

def insert_document(title: str, source: str) -> int:
    sql = "INSERT INTO documents(title, source) VALUES(%s,%s)"
    return exec_sql(sql, (title, source), return_last_id=True)

def insert_chunk(doc_id: int, text: str, metadata: dict) -> int:
    sql = "INSERT INTO chunks(document_id, text, metadata) VALUES(%s,%s,%s)"
    meta_json = json.dumps(metadata, ensure_ascii=False)
    return exec_sql(sql, (doc_id, text, meta_json), return_last_id=True)

def upsert_embedding(chunk_id: int, vector: List[float]):
    # TiDB VECTOR column accepts JSON array literal as the value
    vec_literal = json.dumps(vector, separators=(",", ":"))
    sql = "REPLACE INTO embeddings(chunk_id, embedding) VALUES(%s, %s)"
    exec_sql(sql, (chunk_id, vec_literal))

def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    embs = [d.embedding for d in resp.data]
    # safety check on dim
    if any(len(e) != EMBED_DIM for e in embs):
        raise ValueError(f"Embedding dim mismatch. Expected {EMBED_DIM}.")
    return embs

# ---------- PDF ingestion

def ingest_pdfs(pdf_dir: str = "data/manuals"):
    p = Path(pdf_dir)
    if not p.exists():
        print(f"skip PDFs, folder not found: {pdf_dir}")
        return

    for pdf in sorted(p.glob("*.pdf")):
        print(f"[PDF] ingest {pdf.name}")
        doc_id = insert_document(title=pdf.name, source=str(pdf))

        reader = PdfReader(str(pdf))
        full = " ".join((page.extract_text() or "") for page in reader.pages)
        chunks = list(chunk_text(full, max_chars=1200))
        if not chunks:
            print(f"  no text extracted from {pdf.name}")
            continue

        # Insert chunks
        chunk_ids = []
        for ch in chunks:
            cid = insert_chunk(doc_id, ch, metadata={"vendor": "demo", "type": "manual"})
            chunk_ids.append(cid)

        # Embed in batches to be safe
        BATCH = 64
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i:i+BATCH]
            embs = embed_texts(batch)
            for j, emb in enumerate(embs):
                upsert_embedding(chunk_ids[i + j], emb)
        print(f"  inserted chunks: {len(chunks)}")

# ---------- tickets ingestion

def ingest_tickets(jsonl_path: str = "data/tickets.jsonl"):
    fp = Path(jsonl_path)
    if not fp.exists():
        print(f"skip tickets, file not found: {jsonl_path}")
        return

    lines = fp.read_text(encoding="utf-8").splitlines()
    print(f"[TICKETS] ingest {len(lines)} rows")

    for idx, line in enumerate(lines, 1):
        if not line.strip():
            continue
        t = json.loads(line)
        title = f"ticket {t.get('id', idx)} {t.get('error_code', '')}"
        source = f"ticket:{t.get('id', idx)}"
        doc_id = insert_document(title=title, source=source)

        body = (
            f"[service:{t.get('service','')}] "
            f"[component:{t.get('component','')}] "
            f"[error_code:{t.get('error_code','')}] "
            f"[version:{t.get('version','')}] "
            f"[env:{t.get('env','')}] "
            f"summary: {t.get('summary','')} details: {t.get('details','')}"
        )
        metadata = {
            "service": t.get("service"),
            "component": t.get("component"),
            "error_code": t.get("error_code"),
            "version": t.get("version"),
            "env": t.get("env"),
            "type": "ticket"
        }
        cid = insert_chunk(doc_id, body, metadata)

        # single embed per ticket chunk
        emb = embed_texts([body])[0]
        upsert_embedding(cid, emb)

        if idx % 10 == 0:
            print(f"  processed {idx} tickets")

# ---------- simple sanity queries

def sanity_counts():
    n_docs = query_all("SELECT COUNT(*) FROM documents")[0][0]
    n_chunks = query_all("SELECT COUNT(*) FROM chunks")[0][0]
    n_embs = query_all("SELECT COUNT(*) FROM embeddings")[0][0]
    print(f"[COUNTS] documents={n_docs} chunks={n_chunks} embeddings={n_embs}")

# ---------- main

if __name__ == "__main__":
    print("starting ingest...")
    ingest_pdfs("data/manuals")
    ingest_tickets("data/tickets.jsonl")
    sanity_counts()
    print("ingest complete")
