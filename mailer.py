"""SMTP delivery with per-fire threading."""

from __future__ import annotations

import logging
import smtplib
import ssl
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Iterator

from config import SmtpConfig
from render import Message

logger = logging.getLogger(f"fogosptalerts.{__name__}")


class MailError(Exception):
    """Delivery failed; the caller should not advance state for this event."""


class Mailer:
    def __init__(self, config: SmtpConfig, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run

    @property
    def domain(self) -> str:
        """Domain used to mint Message-IDs — keeps threading stable across restarts."""
        _, _, domain = self.config.sender.rpartition("@")
        return domain.strip("> ").strip() or "fogosptalerts.local"

    @contextmanager
    def _connect(self) -> Iterator[smtplib.SMTP]:
        cfg = self.config
        context = ssl.create_default_context()

        if cfg.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                cfg.host, cfg.port, timeout=cfg.timeout, context=context
            )
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)

        with server:
            server.ehlo()
            if not cfg.use_ssl and cfg.use_starttls:
                server.starttls(context=context)
                server.ehlo()
            if cfg.username:
                server.login(cfg.username, cfg.password)
            yield server

    def send(self, message: Message, thread_root: str | None = None, is_root: bool = False) -> None:
        """Deliver one message. `thread_root` groups a fire's updates into a thread."""
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = self.config.sender
        # Recipients go in Bcc, which send_message() strips before transmitting,
        # so the delivered copy names only the sender and nobody learns who else
        # is on the list. Delivery itself is driven by the explicit envelope below.
        mail["To"] = self.config.sender
        mail["Bcc"] = ", ".join(self.config.recipients)
        mail["Date"] = formatdate(localtime=True)
        mail["Auto-Submitted"] = "auto-generated"
        mail["X-Mailer"] = "FogosPtAlerts"

        if thread_root and is_root:
            mail["Message-ID"] = thread_root
        else:
            mail["Message-ID"] = make_msgid(domain=self.domain)
            if thread_root:
                mail["In-Reply-To"] = thread_root
                mail["References"] = thread_root

        mail.set_content(message.text_body)
        mail.add_alternative(message.html_body, subtype="html")

        if self.dry_run:
            logger.info(
                "[dry-run] would send %r to %s", message.subject, ", ".join(self.config.recipients)
            )
            return

        try:
            with self._connect() as server:
                # Pass the envelope explicitly: left to itself send_message()
                # derives it from To + Cc + Bcc, which would deliver twice to
                # whoever is both a recipient and the sender.
                server.send_message(mail, to_addrs=self.config.recipients)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise MailError(f"SMTP delivery failed: {exc}") from exc

        logger.info("Sent %r to %d recipient(s)", message.subject, len(self.config.recipients))

    def verify(self) -> bool:
        """Probe the SMTP server at startup so misconfiguration fails loudly."""
        if self.dry_run:
            logger.info("Dry run enabled — skipping SMTP check")
            return True
        try:
            with self._connect():
                pass
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            logger.error("SMTP check failed for %s:%s — %s", self.config.host, self.config.port, exc)
            return False
        logger.info("SMTP check OK (%s:%s)", self.config.host, self.config.port)
        return True
