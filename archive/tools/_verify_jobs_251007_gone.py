"""Verifica que jobs 251007 / VANTRAN251007 ya no existan en VSM."""
import json
import urllib.error
import urllib.parse
import urllib.request
import os
import psycopg2
from psycopg2.extras import RealDictCursor

BASE = "http://192.168.2.80:8003"
jobs = ("251007", "VANTRAN251007")
for job in jobs:
    url = f"{BASE}/jobs/by-number/{urllib.parse.quote(job)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            print(job, "API", r.status, r.read()[:200])
    except urllib.error.HTTPError as e:
        print(job, "API", e.code)
    except Exception as e:
        print(job, "API ERR", e)

cfg = dict(
    host=os.getenv("VSM_DB_HOST", "192.168.2.80"),
    port=os.getenv("VSM_DB_PORT", "5437"),
    dbname=os.getenv("VSM_DB_NAME", "foldertree"),
    user=os.getenv("VSM_DB_USER", "user"),
    password=os.getenv("VSM_DB_PASSWORD", "password"),
    connect_timeout=15,
)
conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("SELECT id, job_number, status FROM jobs WHERE TRIM(job_number)=ANY(%s)", (list(jobs),))
print("DB jobs:", [dict(r) for r in cur.fetchall()])
conn.close()
