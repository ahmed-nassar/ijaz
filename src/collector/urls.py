"""URL normalization: one stable form per article link (layer 3)."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

# Query parameters that only track the reader and never change the page.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gclsrc", "msclkid", "yclid", "twclid", "igshid",
    "mc_cid", "mc_eid", "ocid", "cmpid", "cmp", "ncid", "ref", "ref_src", "referrer",
    "smid", "sr_share", "fromrss", "spm", "_ga", "_gl",
    "s_cid", "icid", "wt.mc_id", "at_medium", "at_campaign",
}
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "__twitter")

DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in TRACKING_PARAMS or k.startswith(TRACKING_PREFIXES)


def normalize_url(url: str, base: str | None = None) -> str:
    """Return a canonical form of an article URL.

    - resolves relative links against ``base``
    - lowercases scheme and host, drops default ports and the fragment
    - removes tracking parameters and sorts the rest
    - removes a trailing ``/amp`` path segment and ``?amp`` flags
    - keeps percent-encoding consistent, so Arabic slugs compare equal however encoded
    """
    from urllib.parse import urljoin

    url = url.strip()
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("amp."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"

    path = quote(unquote(parts.path), safe="/:@!$&'()*+,;=-._~%")
    if path.endswith("/amp") or path.endswith("/amp/"):
        path = path[: path.rfind("/amp")] or "/"
    if not path:
        path = "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k) and k.lower() not in {"amp", "outputtype"}
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{(parts.netloc or '').lower()}"


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def same_site(a: str, b: str) -> bool:
    """True when two URLs share a registrable-looking host (ignores www and amp)."""

    def strip(h: str) -> str:
        for prefix in ("www.", "amp.", "m."):
            if h.startswith(prefix):
                h = h[len(prefix):]
        return h

    ha, hb = strip(host_of(a)), strip(host_of(b))
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)
