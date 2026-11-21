"""Auto-detect user location via IP geolocation (no API key needed)."""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

_cache: dict[str, object] = {}
_lock = threading.Lock()
_CACHE_TTL = 3600


def _fetch() -> dict[str, str]:
    import httpx

    for url in (
        "https://ipapi.co/json/",
        "http://ip-api.com/json/?fields=city,regionName,country,lat,lon,timezone",
    ):
        try:
            with httpx.Client(timeout=5) as c:
                r = c.get(url)
                r.raise_for_status()
                data = r.json()
                city = data.get("city") or ""
                region = data.get("region") or data.get("regionName") or ""
                country = data.get("country_name") or data.get("country") or ""
                lat = data.get("latitude") or data.get("lat") or ""
                lon = data.get("longitude") or data.get("lon") or ""
                tz = data.get("timezone") or ""
                if city:
                    return {
                        "city": city,
                        "region": region,
                        "country": country,
                        "lat": str(lat),
                        "lon": str(lon),
                        "timezone": tz,
                        "display": f"{city}, {region}, {country}".strip(", "),
                    }
        except Exception:
            log.debug("location fetch from %s failed", url, exc_info=True)
    return {}


def get() -> dict[str, str]:
    with _lock:
        cached = _cache.get("loc")
        ts = _cache.get("ts", 0)
        if cached and (time.time() - ts) < _CACHE_TTL:  # type: ignore[operator]
            return cached  # type: ignore[return-value]

    result = _fetch()
    if result:
        with _lock:
            _cache["loc"] = result
            _cache["ts"] = time.time()
    return result


def city() -> str:
    loc = get()
    return loc.get("city", "")


def display() -> str:
    loc = get()
    return loc.get("display", "")
