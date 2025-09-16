import os
import sys
import json
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from app.db import exec_sql

load_dotenv()
client = OpenAI()

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _clean_filters(src: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Keep only known keys and drop empty strings/None. Values -> str."""
    if not src:
        return {}
    out: Dict[str, str] = {}
    for k in ("service", "error_code", "env", "keyword"):
        v = src.get(k)
        if isinstance(v, str):
            v = v.strip()
        if v:
            out[k] = str(v)
    return out


# -----------------------------------------------------------------------------
# Core search
# -----------------------------------------------------------------------------
def hybrid_search(query_text: str, top_k: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    KNN-first, then SQL post-filtering on metadata/text.

    Implementation: **Variant A** (single embedding param)
      - Uses CAST(%s AS VECTOR(dim)) for the query embedding
      - Orders by the aliased `score` (cosine distance), ascending
      - Supports optional filters: service, error_code, env (case-insensitive), keyword (LIKE)

    Params passed to SQL in order: [json.dumps(emb), *filter_params, top_k]
    """
    flt = _clean_filters(filters)

    # Build WHERE for post-filter (applied after initial KNN buffer)
    where_clauses: List[str] = []
    params: List[Any] = []

    # Case-insensitive comparisons for JSON fields
    if "service" in flt:
        where_clauses.append("LOWER(JSON_UNQUOTE(JSON_EXTRACT(t.metadata, '$.service'))) = LOWER(%s)")
        params.append(flt["service"]) 
    if "error_code" in flt:
        where_clauses.append("LOWER(JSON_UNQUOTE(JSON_EXTRACT(t.metadata, '$.error_code'))) = LOWER(%s)")
        params.append(flt["error_code"]) 
    if "env" in flt:
        where_clauses.append("LOWER(JSON_UNQUOTE(JSON_EXTRACT(t.metadata, '$.env'))) = LOWER(%s)")
        params.append(flt["env"]) 
    if "keyword" in flt:
        where_clauses.append("LOWER(t.text) LIKE %s")
        params.append(f"%{flt['keyword'].lower()}%")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Embed query
    emb = client.embeddings.create(model=EMBED_MODEL, input=[query_text]).data[0].embedding
    dim = len(emb)

    # Use a KNN buffer >= top_k to let post-filter still have headroom
    knn_buffer = max(int(top_k or 5), 50)

    sql = f"""
    SELECT * FROM (
      SELECT
        c.id AS chunk_id,
        c.text,
        c.metadata,
        VEC_COSINE_DISTANCE(e.embedding, %s) AS score
      FROM embeddings e
      JOIN chunks c ON c.id = e.chunk_id
      ORDER BY VEC_COSINE_DISTANCE(e.embedding, %s)
      LIMIT {knn_buffer}
    ) t
    {where_sql}
    ORDER BY score
    LIMIT %s
    """


    rows = exec_sql(sql, [json.dumps(emb), json.dumps(emb), *params, top_k]) or []

    # rows come back as tuples in order of SELECT from subquery
    out: List[Dict[str, Any]] = []
    for r in rows:
        chunk_id = r[0]
        text = r[1]
        meta_raw = r[2]
        score = r[3]
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, (str, bytes, bytearray)) else (meta_raw or {})
        except Exception:
            meta = {}
        out.append({
            "chunk_id": chunk_id,
            "text": text,
            "metadata": meta or {},
            "score": float(score),
        })
    return out


# -----------------------------------------------------------------------------
# CLI entry (handy for debugging)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.search '<query>' [top_k or filters_json]")
        print("Examples:")
        print("  python -m app.search 'AUTH-500 login timeout'")
        print("  python -m app.search 'login timeout' '{\"service\":\"auth\",\"error_code\":\"AUTH-500\"}'")
        sys.exit(1)

    query = sys.argv[1]
    filters = None
    top_k = 5
    if len(sys.argv) >= 3:
        if sys.argv[2].strip().startswith("{"):
            filters = json.loads(sys.argv[2])
        else:
            top_k = int(sys.argv[2])

    hits = hybrid_search(query, top_k=top_k, filters=filters)
    print(f"\nTop {len(hits)} results:")
    for h in hits:
        print(f"- chunk_id={h['chunk_id']} score={h['score']:.4f}")
        print(f"  meta={h['metadata']}")
        print(f"  text={(h['text'] or '')[:200]}...\n")
