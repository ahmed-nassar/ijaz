"""One worker at a time: a lease row in the database, renewed by its holder each round.

No connection is held between rounds (a session-level advisory lock would keep one
open and stop Neon from sleeping), so the lease has an expiry instead: if the holder
stops renewing it, another worker takes it over at its next round.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import WorkerLease


def worker_id() -> str:
    """Fly sets FLY_MACHINE_ID on every machine; elsewhere, host name and process id."""
    return os.environ.get("FLY_MACHINE_ID") or f"{socket.gethostname()}:{os.getpid()}"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def claim(sessions: sessionmaker[Session], name: str, holder: str, seconds: float, now: datetime | None = None) -> bool:
    """Take or renew the lease. True if ``holder`` now holds it for ``seconds``."""
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(seconds=seconds)
    with sessions() as session:
        lease = session.scalar(select(WorkerLease).where(WorkerLease.name == name).with_for_update())
        if lease is None:
            session.add(WorkerLease(name=name, holder=holder, expires_at=until))
            try:
                session.commit()
                return True
            except IntegrityError:  # another worker created it first
                session.rollback()
                return False
        if lease.holder != holder and _utc(lease.expires_at) > now:
            return False
        lease.holder, lease.expires_at = holder, until
        session.commit()
        return True


def holder(sessions: sessionmaker[Session], name: str) -> tuple[str, datetime] | None:
    with sessions() as session:
        lease = session.get(WorkerLease, name)
        return (lease.holder, _utc(lease.expires_at)) if lease else None
