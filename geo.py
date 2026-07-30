"""Geographic helpers."""

from __future__ import annotations

import math
import unicodedata

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return round(EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


def normalize(text: str) -> str:
    """Casefold and strip accents, so 'Óbidos' matches 'obidos'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def bearing_label(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Compass direction from point 1 to point 2, in Portuguese abbreviations."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    y = math.sin(dlambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlambda)
    degrees = (math.degrees(math.atan2(y, x)) + 360) % 360

    points = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return points[round(degrees / 45) % 8]
