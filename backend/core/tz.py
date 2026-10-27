"""Best-effort recipient time zone, so a 9-to-6 send window means 9-to-6 for the person reading it.

Only unambiguous signals are used: a country-code domain of a single-zone country, or a plan whose regions all
sit in one zone. Anything else returns None and the sender's own zone applies. Approximate on purpose."""
from __future__ import annotations

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TLD_ZONES = {
    "in": "Asia/Kolkata", "uk": "Europe/London", "ie": "Europe/Dublin", "de": "Europe/Berlin", "fr": "Europe/Paris",
    "nl": "Europe/Amsterdam", "es": "Europe/Madrid", "it": "Europe/Rome", "se": "Europe/Stockholm", "ch": "Europe/Zurich",
    "pl": "Europe/Warsaw", "jp": "Asia/Tokyo", "kr": "Asia/Seoul", "sg": "Asia/Singapore", "hk": "Asia/Hong_Kong",
    "ae": "Asia/Dubai", "il": "Asia/Jerusalem", "nz": "Pacific/Auckland", "za": "Africa/Johannesburg",
    "ng": "Africa/Lagos", "ke": "Africa/Nairobi", "pk": "Asia/Karachi", "bd": "Asia/Dhaka", "lk": "Asia/Colombo",
    "id": "Asia/Jakarta", "my": "Asia/Kuala_Lumpur", "th": "Asia/Bangkok", "vn": "Asia/Ho_Chi_Minh", "ph": "Asia/Manila",
}
REGION_ZONES = {
    "india": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "pune": "Asia/Kolkata", "hyderabad": "Asia/Kolkata", "chennai": "Asia/Kolkata",
    "gurgaon": "Asia/Kolkata", "gurugram": "Asia/Kolkata", "noida": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "london": "Europe/London", "uk": "Europe/London", "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "paris": "Europe/Paris", "singapore": "Asia/Singapore", "dubai": "Asia/Dubai", "tokyo": "Asia/Tokyo",
    "new york": "America/New_York", "nyc": "America/New_York", "boston": "America/New_York",
    "san francisco": "America/Los_Angeles", "bay area": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "austin": "America/Chicago", "chicago": "America/Chicago",
}


def _zone(name: str | None) -> ZoneInfo | None:
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def infer_zone(email_domain: str, regions: list[str] | None = None) -> ZoneInfo | None:
    tld = email_domain.strip().lower().rsplit(".", 1)[-1]
    if (z := _zone(TLD_ZONES.get(tld))) is not None:
        return z
    names = {REGION_ZONES.get(r.strip().lower()) for r in (regions or []) if r.strip()}
    if len(names) == 1 and None not in names:
        return _zone(next(iter(names)))
    return None


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def in_send_window(now_epoch: float, start: str, end: str, send_days: list[int], zone: ZoneInfo | None) -> bool:
    """Is `now` inside [start, end) on an allowed weekday (Mon=0), in `zone` (or the machine's zone)?"""
    local = datetime.fromtimestamp(now_epoch, tz=zone) if zone else datetime.fromtimestamp(now_epoch).astimezone()
    return local.weekday() in send_days and _hhmm(start) <= local.time().replace(tzinfo=None) < _hhmm(end)


def local_day_start(now_epoch: float, zone: ZoneInfo | None = None) -> float:
    """Epoch of the most recent local midnight (the sender's day, for daily quotas)."""
    local = datetime.fromtimestamp(now_epoch, tz=zone) if zone else datetime.fromtimestamp(now_epoch).astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
