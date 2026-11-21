"""FastAPI read API used by the website (server to server; not a public API yet)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from collector.config import get_settings
from collector.db import make_engine, session_factory

from .river import BadCursor, filter_options, river_page

app = FastAPI(title="News read API", docs_url=None, redoc_url=None, openapi_url=None)


@lru_cache(maxsize=1)
def _sessions() -> sessionmaker[Session]:
    engine = make_engine(get_settings().database_url)
    return session_factory(engine)


def get_session() -> Iterator[Session]:
    with _sessions()() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


class CopyOut(BaseModel):
    publisher: str
    publisher_ar: str | None
    url: str


class ItemOut(BaseModel):
    id: int
    title: str
    url: str
    language: str
    time: str  # ISO 8601, UTC
    publisher_slug: str
    publisher: str
    publisher_ar: str | None
    copies: list[CopyOut]


class RiverOut(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/river", response_model=RiverOut)
def get_river(
    session: SessionDep,
    response: Response,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    lang: Annotated[str | None, Query(pattern=r"^[a-z]{2,3}$")] = None,
    country: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    publisher: Annotated[str | None, Query(pattern=r"^[a-z0-9-]{1,100}$")] = None,
) -> RiverOut:
    try:
        page = river_page(session, limit=limit, cursor=cursor, language=lang, country=country, publisher=publisher)
    except BadCursor:
        raise HTTPException(status_code=400, detail="invalid cursor") from None
    response.headers["Cache-Control"] = "public, max-age=30"
    return RiverOut(
        items=[
            ItemOut(
                id=i.id, title=i.title, url=i.url, language=i.language, time=i.time.isoformat(),
                publisher_slug=i.publisher_slug, publisher=i.publisher, publisher_ar=i.publisher_ar,
                copies=[CopyOut(publisher=c.publisher, publisher_ar=c.publisher_ar, url=c.url) for c in i.copies],
            )
            for i in page.items
        ],
        next_cursor=page.next_cursor,
    )


FILTERS_TTL = 300.0  # seconds; the lists change only when a new language, country or publisher appears
_filters_cache: tuple[float, dict] | None = None
_filters_lock = threading.Lock()


@app.get("/api/river/filters")
def get_filters(session: SessionDep, response: Response) -> dict:
    # The lists scan the article table (about 80 ms at 100,000 rows), so each process
    # computes them at most once every five minutes.
    global _filters_cache
    response.headers["Cache-Control"] = "public, max-age=300"
    with _filters_lock:
        if _filters_cache is None or time.monotonic() - _filters_cache[0] > FILTERS_TTL:
            _filters_cache = (time.monotonic(), filter_options(session))
        return _filters_cache[1]
