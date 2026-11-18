"""Independent bank sync worker. Run beside the API, not inside HTTP request handling.

    cd backend && python worker.py --once       # one queued sync
    cd backend && python worker.py --poll 30    # long-running process
"""
import argparse
import logging
import time

import appdb
import sync_jobs
import maintenance


def heartbeat():
    """Record observed worker activity without user data or provider secrets."""
    with appdb.connect() as c:
        appdb.execute(c, """INSERT INTO app_worker_heartbeat(service,last_seen_at)
            VALUES ('bank-sync',?) ON CONFLICT(service) DO UPDATE SET
            last_seen_at=excluded.last_seen_at""", (int(time.time()),))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ahead.bank.worker")


def process_one():
    job = sync_jobs.claim()
    if not job:
        return False
    try:
        result = sync_jobs.run_claimed(job)
        log.info("Bank sync succeeded; job_id=%s changes=%s", job["id"], result.get("added", 0) + result.get("modified", 0) + result.get("removed", 0))
    except Exception:
        # Don't print exception details: providers may expose sensitive information.
        log.warning("Bank sync failed; retry scheduled; job_id=%s", job["id"])
    return True


def main():
    parser = argparse.ArgumentParser(description="Ahead bank transaction sync worker")
    parser.add_argument("--once", action="store_true", help="Process one queued job and exit")
    parser.add_argument("--poll", type=int, default=30, help="Polling interval in seconds (minimum 5)")
    args = parser.parse_args()
    if args.once:
        heartbeat()
        maintenance.prune_expired()
        sync_jobs.schedule_stale()
        process_one()
        return
    last_cleanup = 0
    while True:
        heartbeat()
        now = time.monotonic()
        if now - last_cleanup >= 3600:
            try:
                maintenance.prune_expired()
                last_cleanup = now
            except Exception:
                log.warning("Security-record expiry sweep failed; retry on next poll")
        sync_jobs.schedule_stale()
        if not process_one():
            time.sleep(max(5, args.poll))


if __name__ == "__main__":
    main()
