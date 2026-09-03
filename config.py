"""Environment-driven configuration, validated once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from geo import normalize

VERSION = "2.1.0"

# Declared to fogos.pt on the API access request, so it must not drift from
# whatever was submitted there — the operator matches requests against it.
DEFAULT_USER_AGENT = "FogosPtAlerts/2.0 (+https://github.com/xhico/FogosPtAlerts)"


class ConfigError(Exception):
    """Raised when the environment is missing or malformed."""


_TRUTHY = {"1", "true", "yes", "on"}


def _raw(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _float(name: str, default: float | None = None) -> float:
    value = _raw(name)
    if value is None:
        if default is None:
            raise ConfigError(f"{name} is required but not set")
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _int(name: str, default: int | None = None) -> int:
    return int(_float(name, default))


def _bool(name: str, default: bool) -> bool:
    value = _raw(name)
    return default if value is None else value.casefold() in _TRUTHY


def _csv(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in (_raw(name, default) or "").split(",") if part.strip()]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    use_starttls: bool
    use_ssl: bool
    sender: str
    recipients: list[str]
    timeout: int = 30


@dataclass(frozen=True)
class Config:
    center_lat: float
    center_lon: float
    max_distance_km: float
    locations: list[str]
    poll_minutes: int
    min_severity: str
    heartbeat_hours: float
    state_dir: str
    api_url: str
    api_key: str
    user_agent: str
    log_level: str
    smtp: SmtpConfig
    dry_run: bool
    locations_normalized: list[str] = field(default_factory=list, repr=False)

    @property
    def state_file(self) -> str:
        return os.path.join(self.state_dir, "state.json")

    @property
    def poll_seconds(self) -> int:
        return self.poll_minutes * 60


SEVERITY_ORDER = ["info", "elevated", "major"]


def _load_dotenv_if_present() -> None:
    """Convenience for local runs, so .env is the single config file.

    In Docker the environment comes from the compose file, and real
    environment variables always win over anything read here.
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except ImportError:
        return

    for candidate in (".env", "stack.env"):
        if os.path.exists(candidate):
            load_dotenv(candidate)
            return


def load() -> Config:
    """Read the environment into a validated Config, or raise ConfigError."""
    _load_dotenv_if_present()

    locations = _csv("FOGOS_LOCATIONS")
    max_distance = _float("FOGOS_MAX_DISTANCE_KM", 0.0)

    if max_distance <= 0 and not locations:
        raise ConfigError(
            "Nothing to monitor: set FOGOS_MAX_DISTANCE_KM above 0, FOGOS_LOCATIONS, or both"
        )

    center_lat = _float("FOGOS_CENTER_LAT", 0.0)
    center_lon = _float("FOGOS_CENTER_LON", 0.0)
    if max_distance > 0 and center_lat == 0.0 and center_lon == 0.0:
        raise ConfigError(
            "FOGOS_MAX_DISTANCE_KM is set but FOGOS_CENTER_LAT/FOGOS_CENTER_LON are not"
        )
    if not -90 <= center_lat <= 90 or not -180 <= center_lon <= 180:
        raise ConfigError(f"Center point out of range: ({center_lat}, {center_lon})")

    poll_minutes = _int("FOGOS_POLL_MINUTES", 5)
    if poll_minutes < 1:
        raise ConfigError("FOGOS_POLL_MINUTES must be at least 1 (be kind to the upstream API)")

    min_severity = (_raw("FOGOS_MIN_SEVERITY", "info") or "info").casefold()
    if min_severity not in SEVERITY_ORDER:
        raise ConfigError(f"FOGOS_MIN_SEVERITY must be one of {SEVERITY_ORDER}")

    recipients = _csv("EMAIL_TO")
    if not recipients:
        raise ConfigError("EMAIL_TO is required (comma-separated list of recipients)")

    dry_run = _bool("FOGOS_DRY_RUN", False)
    smtp_host = _raw("SMTP_HOST", "") or ""
    if not smtp_host and not dry_run:
        raise ConfigError("SMTP_HOST is required unless FOGOS_DRY_RUN=true")

    use_ssl = _bool("SMTP_SSL", False)
    smtp = SmtpConfig(
        host=smtp_host,
        port=_int("SMTP_PORT", 465 if use_ssl else 587),
        username=_raw("SMTP_USERNAME", "") or "",
        password=_raw("SMTP_PASSWORD", "") or "",
        use_starttls=_bool("SMTP_STARTTLS", not use_ssl),
        use_ssl=use_ssl,
        sender=_raw("EMAIL_FROM") or _raw("SMTP_USERNAME", "") or "",
        recipients=recipients,
        timeout=_int("SMTP_TIMEOUT", 30),
    )
    if not smtp.sender and not dry_run:
        raise ConfigError("EMAIL_FROM (or SMTP_USERNAME) is required to set the From address")

    return Config(
        center_lat=center_lat,
        center_lon=center_lon,
        max_distance_km=max_distance,
        locations=locations,
        locations_normalized=[normalize(loc) for loc in locations],
        poll_minutes=poll_minutes,
        min_severity=min_severity,
        heartbeat_hours=_float("FOGOS_HEARTBEAT_HOURS", 24.0),
        state_dir=_raw("FOGOS_STATE_DIR", "/data") or "/data",
        api_url=_raw("FOGOS_API_URL", "https://api-dev.fogos.pt/new/fires") or "",
        api_key=_raw("FOGOS_API_KEY", "") or "",
        user_agent=_raw("FOGOS_USER_AGENT", DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT,
        log_level=(_raw("LOG_LEVEL", "INFO") or "INFO").upper(),
        smtp=smtp,
        dry_run=dry_run,
    )


def describe(config: Config) -> list[str]:
    """Human-readable config summary for the startup log (no secrets)."""
    radius = f"{config.max_distance_km:g} km de ({config.center_lat:.4f}, {config.center_lon:.4f})"
    return [
        f"Raio            : {radius if config.max_distance_km > 0 else 'desativado'}",
        f"Localidades     : {', '.join(config.locations) or 'nenhuma'}",
        f"Intervalo       : {config.poll_minutes} min",
        f"Severidade min. : {config.min_severity}",
        f"Heartbeat       : {f'{config.heartbeat_hours:g}h' if config.heartbeat_hours > 0 else 'desativado'}",
        f"Estado          : {config.state_file}",
        f"User-Agent      : {config.user_agent}",
        # Never log the key itself, only whether one is in play.
        f"X-API-Key       : {'configurada' if config.api_key else 'não definida'}",
        f"SMTP            : {config.smtp.host}:{config.smtp.port} "
        f"({'SSL' if config.smtp.use_ssl else 'STARTTLS' if config.smtp.use_starttls else 'plain'})",
        f"De              : {config.smtp.sender}",
        f"Para            : {', '.join(config.smtp.recipients)}",
        f"Dry run         : {config.dry_run}",
    ]
