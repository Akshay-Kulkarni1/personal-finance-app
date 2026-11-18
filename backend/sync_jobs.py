"""Durable, per-connection bank sync queue. Safe across workers and process restarts.

Leases are claimed with a compare-and-swap UPDATE inside a DB transaction. Webhooks
coalesce updates rather than starting network calls in the request handler.
This is still a lightweight queue; add metrics and managed workers before production.
"""
import time
import uuid

from fastapi import HTTPException

import appdb

LEASE_SECONDS = 3600
MAX_BACKOFF_SECONDS = 3600
MAX_ATTEMPTS = 8  # Persist failures; do not retry indefinitely without intervention.
PERIODIC_SYNC_SECONDS = 6 * 3600


def enqueue(c, user_id, connection_id, *, now=None, manual=False):
    """Queue a sync without bypassing exhausted automatic retries.

    Webhooks and periodic scheduling never reactivate dead-letter jobs. A user
    clicking Sync can explicitly reset the attempt budget. Active leases are
    not stolen; instead their rerun_requested flag is set.
    """
    now = int(time.time()) if now is None else now
    manual = int(manual)
    appdb.execute(c, """INSERT INTO app_bank_sync_jobs
        (id,user_id,connection_id,status,rerun_requested,attempts,next_run_at,created_at,updated_at)
        VALUES (?,?,?,'queued',0,0,?,?,?)
        ON CONFLICT(connection_id) DO UPDATE SET
          rerun_requested=CASE
            WHEN app_bank_sync_jobs.status='running' AND app_bank_sync_jobs.lease_until>? THEN 1
            ELSE 0 END,
          status=CASE
            WHEN app_bank_sync_jobs.status='running' AND app_bank_sync_jobs.lease_until>? THEN 'running'
            WHEN app_bank_sync_jobs.status IN ('dead','needs_attention') AND ?=0 THEN app_bank_sync_jobs.status
            ELSE 'queued' END,
          attempts=CASE WHEN ?=1 AND app_bank_sync_jobs.status='dead' THEN 0 ELSE app_bank_sync_jobs.attempts END,
          last_error=CASE WHEN ?=1 AND app_bank_sync_jobs.status='dead' THEN NULL ELSE app_bank_sync_jobs.last_error END,
          next_run_at=CASE
            WHEN app_bank_sync_jobs.status='running' AND app_bank_sync_jobs.lease_until>? THEN app_bank_sync_jobs.next_run_at
            WHEN app_bank_sync_jobs.status IN ('dead','needs_attention') AND ?=0 THEN app_bank_sync_jobs.next_run_at
            ELSE excluded.next_run_at END,
          updated_at=excluded.updated_at""",
        (str(uuid.uuid4()), user_id, connection_id, now, now,
         now, now, now, manual, manual, manual, now, manual))


def _candidate(c, *, connection_id=None, force=False, now=None):
    now = int(time.time()) if now is None else now
    where = "(status IN ('queued','retry') AND next_run_at<=?) OR (status='running' AND lease_until<=?)"
    args = [now, now]
    if force:
        where = "(status IN ('queued','retry','done')) OR (status='running' AND lease_until<=?)"
        args = [now]
    if connection_id:
        where = f"connection_id=? AND ({where})"
        args.insert(0, connection_id)
    rows = appdb.execute(c, f"SELECT id,user_id,connection_id FROM app_bank_sync_jobs WHERE {where} ORDER BY next_run_at LIMIT 20", args).fetchall()
    for row in rows:
        # CAS: if another worker already took the job, this UPDATE affects zero rows.
        claim_token = str(uuid.uuid4())
        update = appdb.execute(c, """UPDATE app_bank_sync_jobs SET status='running',
            claim_token=?, rerun_requested=0, attempts=attempts+1, lease_until=?, updated_at=?
            WHERE id=? AND
              ((status IN ('queued','retry') AND next_run_at<=?)
               OR (status='running' AND lease_until<=?)
               OR (?=1 AND status='done')
               OR (?=1 AND status IN ('queued','retry')))""",
            (claim_token, now + LEASE_SECONDS, now, row["id"], now, now, int(force), int(force)))
        if update.rowcount == 1:
            return {**dict(row), "claim_token": claim_token}
    return None


def claim(*, connection_id=None, force=False):
    with appdb.connect() as c:
        return _candidate(c, connection_id=connection_id, force=force)


def finish(job_id, claim_token, *, success, error=None, needs_attention=False):
    now = int(time.time())
    with appdb.connect() as c:
        job = appdb.execute(c, "SELECT attempts,rerun_requested,status FROM app_bank_sync_jobs WHERE id=? AND claim_token=?", (job_id, claim_token)).fetchone()
        if not job or job["status"] != "running":
            return
        if success:
            queued = bool(job["rerun_requested"])
            appdb.execute(c, """UPDATE app_bank_sync_jobs SET status=?,rerun_requested=0,
                lease_until=NULL, claim_token=NULL, attempts=0, next_run_at=?,last_error=NULL,
                last_success_at=?,updated_at=? WHERE id=? AND claim_token=?""",
                ("queued" if queued else "done", now, now, now, job_id, claim_token))
        elif needs_attention:
            appdb.execute(c, """UPDATE app_bank_sync_jobs SET status='needs_attention',
                claim_token=NULL, lease_until=NULL, last_error='Reconnection required',
                updated_at=? WHERE id=? AND claim_token=?""", (now, job_id, claim_token))
            appdb.execute(c, """UPDATE app_bank_connections SET status='needs_attention',
                updated_at=? WHERE id=(SELECT connection_id FROM app_bank_sync_jobs WHERE id=?)""", (now, job_id))
        elif job["attempts"] >= MAX_ATTEMPTS:
            # Dead-letter after bounded attempts. Webhooks cannot silently restart
            # this job; users can deliberately retry from the Banks screen.
            appdb.execute(c, """UPDATE app_bank_sync_jobs SET status='dead',
                rerun_requested=0,lease_until=NULL,claim_token=NULL,
                last_error='Automatic retries exhausted. Use Sync to retry.',updated_at=?
                WHERE id=? AND claim_token=?""", (now, job_id, claim_token))
        else:
            delay = min(MAX_BACKOFF_SECONDS, 30 * (2 ** min(job["attempts"] - 1, 7)))
            # Store a stable safe classification, never provider payloads/access tokens.
            appdb.execute(c, """UPDATE app_bank_sync_jobs SET status='retry',
                rerun_requested=0,lease_until=NULL,claim_token=NULL,next_run_at=?,last_error=?,updated_at=?
                WHERE id=? AND claim_token=?""", (now + delay, error or "Sync temporarily unavailable", now, job_id, claim_token))


def run_claimed(job):
    # Lazy import avoids bank -> queue -> bank import cycle.
    import bank
    try:
        result = bank.sync_connection(job["user_id"], job["connection_id"])
    except HTTPException as e:
        finish(job["id"], job["claim_token"], success=False, needs_attention=e.detail == "BANK_RELINK_REQUIRED")
        raise
    except Exception:
        finish(job["id"], job["claim_token"], success=False)
        raise
    else:
        finish(job["id"], job["claim_token"], success=True)
        return result


def run_manual(user_id, connection_id):
    with appdb.connect() as c:
        owned = appdb.execute(c, "SELECT id,status FROM app_bank_connections WHERE id=? AND user_id=?", (connection_id, user_id)).fetchone()
        if not owned:
            raise HTTPException(404, "Connection not found")
        if owned["status"] == "needs_attention":
            raise HTTPException(409, "BANK_RELINK_REQUIRED")
        enqueue(c, user_id, connection_id, manual=True)
    job = claim(connection_id=connection_id, force=True)
    if not job:
        raise HTTPException(409, "This bank connection is already synchronizing")
    return run_claimed(job)


def schedule_stale(now=None):
    """Catch up missing webhooks. Don't override queued work or retry backoff."""
    now = int(time.time()) if now is None else now
    with appdb.connect() as c:
        rows = appdb.execute(c, """SELECT b.id,b.user_id FROM app_bank_connections b
            LEFT JOIN app_bank_sync_jobs j ON j.connection_id=b.id
            WHERE b.status='connected' AND (b.last_synced_at IS NULL OR b.last_synced_at<?)
              AND (j.id IS NULL OR j.status='done')""", (now - PERIODIC_SYNC_SECONDS,)).fetchall()
        for r in rows:
            enqueue(c, r["user_id"], r["id"], now=now)
        return len(rows)
