import os
import pymysql
import json
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return pymysql.connect(
        host=os.getenv("TIDB_HOST"),
        port=int(os.getenv("TIDB_PORT", "4000")),
        user=os.getenv("TIDB_USER"),
        password=os.getenv("TIDB_PASSWORD"),
        database=os.getenv("TIDB_DB"),
        ssl={"ssl": {}}  # TiDB Cloud requires SSL 
    )

def exec_sql(sql, params=None, many=False, return_last_id=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if many and isinstance(params, list):
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params)
            last_id = cur.lastrowid
        conn.commit()
        if return_last_id:
            return last_id
    finally:
        conn.close()

def query_all(sql, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()

def insert_incident(raw_input: str, extracted_text: str = "") -> int:
    sql = "INSERT INTO incidents(source, raw_input, extracted_text) VALUES(%s,%s,%s)"
    return exec_sql(sql, ("api", raw_input, extracted_text), return_last_id=True)

def insert_run(incident_id: int, retrieved_ids, plan_json, final_summary: str, action_status: str) -> int:
    sql = "INSERT INTO runs(incident_id, retrieved_ids, plan, final_summary, action_status) VALUES(%s,%s,%s,%s,%s)"
    return exec_sql(
        sql,
        (
            incident_id,
            json.dumps(retrieved_ids, separators=(",", ":")),
            json.dumps(plan_json, separators=(",", ":")),
            final_summary,
            action_status,
        ),
        return_last_id=True,
    )