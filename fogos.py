"""Client for the fogos.pt occurrences API, plus the Fire model it produces."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx

from config import Config
from geo import bearing_label, haversine_km, normalize

logger = logging.getLogger(f"fogosptalerts.{__name__}")

USER_AGENT = "FogosPtAlerts/2.0 (+https://github.com/xhico/FogosPtAlerts)"

# Generous read budget — the upstream payload carries KML polygons and is slow
# to serialise, but a hung connect should fail fast so the loop can back off.
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# Occurrence lifecycle, in order. Codes 3-6 are active response; from
# "Em Resolução" onwards the incident is winding down rather than escalating.
COOLING_STATUS_CODES = {7, 8, 9, 10}

# Longest place label that still fits a mobile notification preview.
PLACE_BUDGET = 34


class FogosApiError(Exception):
    """The upstream API was unreachable or returned something unusable."""


@dataclass
class Fire:
    """The subset of an occurrence we care about, normalized and enriched."""

    id: str
    started_at: int | None
    status: str
    status_code: int
    district: str
    concelho: str
    freguesia: str
    detail_location: str
    natureza: str
    lat: float
    lng: float
    distance_km: float | None
    bearing: str | None
    man: int
    terrain: int
    aerial: int
    aquatic: int
    important: bool
    matched_by: str

    @property
    def place(self) -> str:
        """Shortest label that still identifies where this is.

        Portuguese freguesia names get very long ("Cambra E Carvalhal De
        Vermilhas"); past the notification-preview budget the concelho alone
        carries more information than a truncated pair.
        """
        parts = list(dict.fromkeys(part for part in (self.concelho, self.freguesia) if part))
        full = " · ".join(parts)
        if len(full) <= PLACE_BUDGET or not parts:
            return full or self.district or "Local desconhecido"
        return parts[0] if len(parts[0]) <= PLACE_BUDGET else parts[0][: PLACE_BUDGET - 1] + "…"

    @property
    def full_place(self) -> str:
        """Untruncated label — the email body has room the subject line does not."""
        parts = dict.fromkeys(part for part in (self.concelho, self.freguesia) if part)
        return " · ".join(parts) or self.district or "Local desconhecido"

    @property
    def detail_url(self) -> str:
        return f"https://fogos.pt/fogo/{self.id}/detalhe"

    @property
    def map_url(self) -> str:
        return f"https://www.google.com/maps/search/?api=1&query={self.lat},{self.lng}"

    @property
    def started_display(self) -> str:
        if not self.started_at:
            return "desconhecido"
        return datetime.fromtimestamp(self.started_at, tz=timezone.utc).strftime("%d/%m %H:%M")

    @property
    def is_cooling(self) -> bool:
        return self.status_code in COOLING_STATUS_CODES

    @property
    def severity(self) -> str:
        """Coarse 'does this deserve to wake me' band."""
        if self.is_cooling:
            return "info"
        if self.aerial >= 2 or self.man >= 50:
            return "major"
        if self.aerial >= 1 or self.man >= 20 or self.important:
            return "elevated"
        return "info"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Fire":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _started_at(raw: dict) -> int | None:
    date_time = raw.get("dateTime") or {}
    if isinstance(date_time, dict) and date_time.get("sec"):
        return _as_int(date_time["sec"])
    try:
        naive = datetime.strptime(f"{raw['date']} {raw['hour']}", "%d-%m-%Y %H:%M")
        return int(naive.replace(tzinfo=timezone.utc).timestamp())
    except (KeyError, TypeError, ValueError):
        return None


_LATLONG_PREFIX = re.compile(r"^\s*LatLong\([^)]*\)\s*[-–]\s*", re.IGNORECASE)


def _clean_detail(raw: dict) -> str:
    """Tidy detailLocation, which is sometimes a raw reverse-geocoder dump."""
    detail = _LATLONG_PREFIX.sub("", str(raw.get("detailLocation") or "")).strip(" .-")
    locality = str(raw.get("localidade") or "").strip()

    # Prefer the shorter label when the detail is an unreadable full address.
    if len(detail) > 90 and locality:
        return locality
    return detail or locality


def _matches_location(raw: dict, wanted: list[str]) -> bool:
    if not wanted:
        return False
    haystack = normalize(
        " | ".join(
            str(raw.get(key) or "")
            for key in ("location", "district", "concelho", "freguesia", "localidade")
        )
    )
    return any(loc in haystack for loc in wanted)


def _build(raw: dict, config: Config) -> Fire | None:
    """Convert one API record into a Fire, or None if it is outside our geofence."""
    try:
        lat, lng = float(raw["lat"]), float(raw["lng"])
    except (KeyError, TypeError, ValueError):
        lat = lng = 0.0

    has_coords = lat != 0.0 or lng != 0.0
    distance = (
        haversine_km(config.center_lat, config.center_lon, lat, lng) if has_coords else None
    )

    by_name = _matches_location(raw, config.locations_normalized)
    by_radius = (
        config.max_distance_km > 0 and distance is not None and distance <= config.max_distance_km
    )
    if not (by_name or by_radius):
        return None

    return Fire(
        id=str(raw.get("id") or raw.get("sadoId") or ""),
        started_at=_started_at(raw),
        status=str(raw.get("status") or "Desconhecido"),
        status_code=_as_int(raw.get("statusCode")),
        district=str(raw.get("district") or ""),
        concelho=str(raw.get("concelho") or ""),
        freguesia=str(raw.get("freguesia") or ""),
        detail_location=_clean_detail(raw),
        natureza=str(raw.get("natureza") or ""),
        lat=lat,
        lng=lng,
        distance_km=distance,
        bearing=(
            bearing_label(config.center_lat, config.center_lon, lat, lng) if has_coords else None
        ),
        man=_as_int(raw.get("man")),
        terrain=_as_int(raw.get("terrain")),
        aerial=_as_int(raw.get("aerial")),
        aquatic=_as_int(raw.get("meios_aquaticos")),
        important=bool(raw.get("important")),
        matched_by="radius" if by_radius else "location",
    )


def build_client() -> httpx.Client:
    """Long-lived client — keeps the connection pool warm across cycles."""
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def fetch(config: Config, client: httpx.Client) -> list[Fire]:
    """Fetch live occurrences and return the ones inside our geofence."""
    try:
        response = client.get(config.api_url)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise FogosApiError(f"request failed: {exc}") from exc
    except ValueError as exc:
        raise FogosApiError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or not payload.get("success"):
        raise FogosApiError("API reported success=false")

    records = payload.get("data")
    if not isinstance(records, list):
        raise FogosApiError("API payload had no 'data' list")

    fires = [fire for raw in records if (fire := _build(raw, config)) and fire.id]
    logger.info("Fetched %d occurrences, %d inside geofence", len(records), len(fires))
    return fires
