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

# Consecutive upstream failures before an outage is worth an email. The dev API
# blips; with backoff this is roughly 7 minutes of sustained trouble.
OUTAGE_ALERT_AFTER_FAILURES = 3

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


def _tracked(st: state_module.State) -> list[fogos.Fire]:
    return sorted(st.fires.values(), key=lambda f: (f.is_cooling, -f.man))


def _elapsed(since: int) -> str:
    minutes = max(0, int(time.time()) - since) // 60
    hours, minutes = divmod(minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h{minutes:02d}" if hours else f"{minutes} min"


def _maybe_heartbeat(cfg: Config, st: state_module.State, mailer: Mailer) -> None:
    """Periodic 'still watching' email — silence must not be ambiguous.

    Runs whether or not the upstream fetch succeeded: an outage is exactly
    when a reader most needs proof the service itself is still alive.
    """
    if cfg.heartbeat_hours <= 0:
        return

    now = int(time.time())
    if now - st.last_heartbeat < cfg.heartbeat_hours * 3600:
        return

    if st.in_outage:
        title, icon = "Ativo, mas sem contacto com a API", "⚠️"
        note = (
            f"O serviço está a correr normalmente, mas não consegue contactar a API "
            f"do fogos.pt há {_elapsed(st.outage_since)}. As ocorrências abaixo são o "
            "último estado conhecido e podem estar desatualizadas."
        )
    else:
        title, icon = "Tudo sob controlo", "📋"
        note = "Resumo periódico. O serviço está ativo e a monitorizar normalmente."

    try:
        mailer.send(render.build_status_message(cfg, _tracked(st), title, note, icon))
        st.last_heartbeat = now
    except MailError as exc:
        logger.error("Heartbeat email failed: %s", exc)


def _note_failure(cfg: Config, st: state_module.State, mailer: Mailer, reason: str) -> None:
    """Record an upstream failure, emailing once when it becomes a real outage.

    A single failed poll is noise — the dev API blips. Only after
    OUTAGE_ALERT_AFTER_FAILURES consecutive failures (roughly 7 minutes once
    backoff is applied) is one email sent, and no more until it recovers.
    """
    now = int(time.time())
    if not st.in_outage:
        st.outage_since = now
    st.outage_failures += 1

    if st.outage_notified or st.outage_failures < OUTAGE_ALERT_AFTER_FAILURES:
        return

    note = (
        f"O serviço está a correr, mas falhou {st.outage_failures} tentativas seguidas "
        f"de contactar a API do fogos.pt (desde há {_elapsed(st.outage_since)}).\n\n"
        f"Último erro: {reason}\n\n"
        "Continuará a tentar, com intervalos progressivamente maiores, e receberá "
        "novo aviso quando o contacto for restabelecido. As ocorrências abaixo são o "
        "último estado conhecido."
    )
    try:
        mailer.send(
            render.build_status_message(
                cfg, _tracked(st), "API do fogos.pt inacessível", note, "⚠️"
            )
        )
        st.outage_notified = True
    except MailError as exc:
        logger.error("Outage email failed: %s", exc)


def _note_recovery(cfg: Config, st: state_module.State, mailer: Mailer) -> None:
    """Clear outage state, emailing once only if we announced the outage."""
    if not st.in_outage:
        return

    announced, downtime = st.outage_notified, _elapsed(st.outage_since)
    st.outage_since = st.outage_failures = 0
    st.outage_notified = False

    if not announced:
        logger.info("Upstream recovered after a brief blip — no email sent")
        return

    note = (
        f"O contacto com a API do fogos.pt foi restabelecido após {downtime} "
        "de indisponibilidade. A monitorização voltou ao normal."
    )
    try:
        mailer.send(
            render.build_status_message(cfg, _tracked(st), "API novamente acessível", note, "✅")
        )
    except MailError as exc:
        logger.error("Recovery email failed: %s", exc)


def run_cycle(cfg: Config, client: httpx.Client, mailer: Mailer) -> bool:
    """Run one full cycle. Returns False if the upstream fetch failed.

    Upstream errors are handled here rather than raised, so the heartbeat and
    the state write still happen during an outage.
    """
    st = state_module.load(cfg.state_file)

    try:
        fires = fogos.fetch(cfg, client)
    except fogos.FogosApiError as exc:
        logger.warning("Fogos API unavailable: %s", exc)
        _note_failure(cfg, st, mailer, str(exc))
        _maybe_heartbeat(cfg, st, mailer)
        state_module.save(cfg.state_file, st)
        return False

    _note_recovery(cfg, st, mailer)

    if not st.initialized:
        _seed(cfg, st, fires, mailer)
        state_module.save(cfg.state_file, st)
        return True

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
    return True


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
                # run_cycle handles upstream errors itself so the heartbeat
                # still fires during an outage; it reports health as a bool.
                consecutive_failures = 0 if run_cycle(cfg, client, mailer) else consecutive_failures + 1
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
