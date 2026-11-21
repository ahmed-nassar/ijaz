"""Start a stopped worker machine when no worker is running (runs on the web machine).

The app has two worker machines, but only one needs to run: the standby stops itself
once it sees the main worker renewing its lease. If the main worker then stops (it
crashed too often, or was stopped), nothing would start the standby again, because
Fly only starts machines on web traffic. This loop does: every few minutes it asks the
Fly Machines API whether any worker is running and, after two checks in a row with
none, starts a stopped one. It never touches the database, so Neon can still sleep.

Needs FLY_MACHINES_TOKEN (an app-scoped token: fly tokens create deploy -a ijaz).
Fly sets FLY_APP_NAME on every machine.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("collector.watchdog")

API = "https://api.machines.dev/v1"
# States in which a worker is running or on its way up.
RUNNING = {"created", "starting", "started", "replacing", "updating"}
STARTABLE = {"stopped", "suspended"}


def worker_machines(client: httpx.Client, app: str, group: str = "worker") -> list[dict]:
    r = client.get(f"{API}/apps/{app}/machines", params={"metadata.fly_process_group": group})
    r.raise_for_status()
    return [
        m for m in r.json()
        if ((m.get("config") or {}).get("metadata") or {}).get("fly_process_group") == group
    ]


def check_once(client: httpx.Client, app: str, misses: int) -> int:
    """One check. Returns the number of consecutive checks with no running worker."""
    machines = worker_machines(client, app)
    if any(m.get("state") in RUNNING for m in machines):
        return 0
    misses += 1
    if misses < 2:
        log.warning("no worker machine running; checking again before starting one")
        return misses
    for m in machines:
        if m.get("state") in STARTABLE:
            r = client.post(f"{API}/apps/{app}/machines/{m['id']}/start")
            r.raise_for_status()
            log.warning("no worker machine running; started %s", m["id"])
            return 0
    log.error("no worker machine running and none can be started: %s",
              [(m.get("id"), m.get("state")) for m in machines])
    return misses


def watch(every_seconds: int = 300) -> int:
    token, app = os.environ.get("FLY_MACHINES_TOKEN"), os.environ.get("FLY_APP_NAME")
    if not token or not app:
        log.warning("worker watchdog off: FLY_MACHINES_TOKEN or FLY_APP_NAME not set")
        return 0
    misses = 0
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        while True:
            try:
                misses = check_once(client, app, misses)
            except Exception:
                log.exception("worker watchdog check failed")
            time.sleep(every_seconds)
