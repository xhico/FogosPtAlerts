"""Entry point: poll, diff, notify, repeat."""

from __future__ import annotations

import logging
import random
import signal
import sys
import threading
import time

import httpx

import config as config_module
import fogos
import render
import state as state_module
from changes import NEW, RESOLVED, UPDATE, Event, detect
from config import VERSION, Config
from mailer import MailError, Mailer

logger = logging.getLogger("fogosptalerts")

# Upper bound of the random buffer added to every sleep, as a fraction of the
# configured interval. 0.25 turns a 1-minute poll into 60-75s.
POLL_JITTER = 0.25

_shutdown = threading.Event()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # httpx logs every request at INFO; we already log the useful part of each
    # cycle, and a 5-minute poll loop would otherwise emit a line forever.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _next_delay(base_seconds: int, consecutive_failures: int) -> int:
    """Seconds to wait before the next cycle.

    Two things happen here. Repeated failures back the interval off, capped at
    four cycles. And every delay carries a random buffer on top, so the service
    never settles into a fixed beat against the upstream API — a fleet of these
    all polling on the exact same second is what gets an IP rate-limited.

    The jitter is added, never subtracted, so the configured interval stays a
    floor: FOGOS_POLL_MINUTES=1 polls every 60-75s, never faster.
    """
    delay = base_seconds * min(2 ** min(consecutive_failures, 2), 4)
    return round(delay * (1 + random.uniform(0, POLL_JITTER)))


def _handle_signal(signum, _frame) -> None:
    logger.info("Received %s — finishing current cycle and exiting", signal.Signals(signum).name)
    _shutdown.set()


def _seed(cfg: Config, st: state_module.State, fires: list[fogos.Fire], mailer: Mailer) -> None:
    """First run: adopt whatever is already burning without emailing about each one.

    Otherwise a fresh container turns every in-progress occurrence into a
    'new fire' alert, which is both alarming and wrong.
    """
    st.fires = {fire.id: fire for fire in fires}
    st.first_seen = {fire.id: int(time.time()) for fire in fires}
    st.initialized = True

    message = render.build_status_message(
        cfg,
        sorted(fires, key=lambda f: (f.is_cooling, -f.man)),
        title="Monitorização iniciada",
        note=(
            f"O serviço arrancou (v{VERSION}) e adotou as ocorrências já em curso "
            "sem gerar alertas individuais. A partir daqui recebe apenas novidades."
        ),
    )
    try:
        mailer.send(message)
    except MailError as exc:
        logger.error("Startup email failed: %s", exc)

    st.last_heartbeat = int(time.time())
    logger.info("Seeded state with %d existing occurrence(s)", len(fires))


def _dispatch(cfg: Config, st: state_module.State, events: list[Event], mailer: Mailer) -> set[str]:
    """Send one email per event. Returns ids whose delivery failed."""
    failed: set[str] = set()

    for event in events:
        fire_id = event.fire.id
        thread_root = st.thread_id(fire_id, mailer.domain)
        is_root = event.kind == NEW

        try:
            mailer.send(render.build_message(event, cfg), thread_root=thread_root, is_root=is_root)
        except MailError as exc:
            logger.error("Could not notify %s for fire %s: %s", event.kind, fire_id, exc)
            failed.add(fire_id)
            continue

        if event.kind == NEW:
            st.first_seen.setdefault(fire_id, int(time.time()))

    return failed


def _maybe_heartbeat(cfg: Config, st: state_module.State, mailer: Mailer) -> None:
    """Periodic 'still watching' email — silence must not be ambiguous."""
    if cfg.heartbeat_hours <= 0:
        return

    now = int(time.time())
    if now - st.last_heartbeat < cfg.heartbeat_hours * 3600:
        return

    tracked = sorted(st.fires.values(), key=lambda f: (f.is_cooling, -f.man))
    message = render.build_status_message(
        cfg,
        tracked,
        title="Tudo sob controlo",
        note="Resumo periódico. O serviço está ativo e a monitorizar normalmente.",
    )
    try:
        mailer.send(message)
        st.last_heartbeat = now
    except MailError as exc:
        logger.error("Heartbeat email failed: %s", exc)


def run_cycle(cfg: Config, client: httpx.Client, mailer: Mailer) -> None:
    st = state_module.load(cfg.state_file)
    fires = fogos.fetch(cfg, client)

    if not st.initialized:
        _seed(cfg, st, fires, mailer)
        state_module.save(cfg.state_file, st)
        return

    events = detect(fires, st.fires, cfg.min_severity)
    if events:
        counts = {kind: sum(1 for e in events if e.kind == kind) for kind in (NEW, UPDATE, RESOLVED)}
        logger.info("Changes: %d new, %d updated, %d resolved", *counts.values())

    failed = _dispatch(cfg, st, events, mailer)

    # Only track fires we have actually reported on, so an occurrence held back
    # by FOGOS_MIN_SEVERITY still counts as new if it later escalates.
    reported = {e.fire.id for e in events if e.kind in (NEW, UPDATE)}
    next_fires: dict[str, fogos.Fire] = {}
    for fire in fires:
        if fire.id not in st.fires and fire.id not in reported:
            continue
        # A failed send leaves the old snapshot in place so the next cycle retries.
        next_fires[fire.id] = st.fires[fire.id] if fire.id in failed and fire.id in st.fires else fire

    # Keep resolved-but-unnotified fires around for another attempt.
    for fire_id in failed:
        if fire_id not in next_fires and fire_id in st.fires:
            next_fires[fire_id] = st.fires[fire_id]

    st.fires = next_fires
    st.prune(set(next_fires))
    _maybe_heartbeat(cfg, st, mailer)
    state_module.save(cfg.state_file, st)


def main() -> int:
    try:
        cfg = config_module.load()
    except config_module.ConfigError as exc:
        _setup_logging("INFO")
        logger.error("Configuration error: %s", exc)
        return 2

    _setup_logging(cfg.log_level)
    logger.info("FogosPT Alerts v%s", VERSION)
    for line in config_module.describe(cfg):
        logger.info("  %s", line)

    mailer = Mailer(cfg.smtp, dry_run=cfg.dry_run)
    if not mailer.verify():
        logger.error("Refusing to start with a broken SMTP configuration")
        return 3

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    consecutive_failures = 0

    with fogos.build_client(cfg) as client:
        while not _shutdown.is_set():
            try:
                run_cycle(cfg, client, mailer)
                consecutive_failures = 0
            except fogos.FogosApiError as exc:
                consecutive_failures += 1
                logger.warning("Fogos API unavailable (attempt %d): %s", consecutive_failures, exc)
            except Exception:
                consecutive_failures += 1
                logger.exception("Unhandled error during cycle")

            delay = _next_delay(cfg.poll_seconds, consecutive_failures)
            logger.debug("Sleeping %ds", delay)
            _shutdown.wait(delay)

    logger.info("Stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
