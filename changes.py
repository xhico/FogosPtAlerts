"""Turn two snapshots of the world into a list of things worth an email.

The guiding rule: an occurrence's resource counts jitter constantly (20 -> 21
operacionais), and alerting on every delta makes the inbox useless. Only
changes that would alter a reader's decision are promoted to an event.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import SEVERITY_ORDER
from fogos import Fire

# A resource change counts as meaningful when it moves at least this fraction
# AND this many units, so both small and large fires get sensible thresholds.
RELATIVE_THRESHOLD = 0.25
ABSOLUTE_THRESHOLD = 5

NEW = "new"
UPDATE = "update"
RESOLVED = "resolved"


@dataclass
class Change:
    """One field that moved, in reader-facing terms."""

    label: str
    old: str
    new: str
    headline: str
    escalation: int = 0  # 1 worse, -1 better, 0 neutral


@dataclass
class Event:
    kind: str
    fire: Fire
    previous: Fire | None = None
    changes: list[Change] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if self.kind == RESOLVED:
            return "info"
        return self.fire.severity

    @property
    def headline(self) -> str:
        """The single most important thing that happened, for the subject line."""
        if not self.changes:
            return ""
        worst = max(self.changes, key=lambda c: (c.escalation, c.label == "Estado"))
        return worst.headline


def _severity_rank(fire: Fire) -> int:
    return SEVERITY_ORDER.index(fire.severity)


def _resource_change(label: str, old: int, new: int) -> Change | None:
    delta = new - old
    if delta == 0:
        return None

    # Crossing zero always matters — aircraft arriving or leaving is the story.
    crossed_zero = (old == 0) != (new == 0)
    significant = abs(delta) >= max(ABSOLUTE_THRESHOLD, round(old * RELATIVE_THRESHOLD))
    if not (crossed_zero or significant):
        return None

    verb = "reforço" if delta > 0 else "redução"
    if crossed_zero and new == 0:
        headline = f"{label} retirados"
    elif crossed_zero:
        headline = f"{label} no local ({new})"
    else:
        headline = f"{verb} de {label.lower()} ({old} → {new})"

    return Change(
        label=label,
        old=str(old),
        new=str(new),
        headline=headline,
        escalation=1 if delta > 0 else -1,
    )


def _diff(previous: Fire, current: Fire) -> list[Change]:
    changes: list[Change] = []

    if previous.status_code != current.status_code or previous.status != current.status:
        escalating = current.status_code < previous.status_code
        changes.append(
            Change(
                label="Estado",
                old=previous.status,
                new=current.status,
                headline=current.status,
                escalation=1 if escalating else -1,
            )
        )

    for label, old, new in (
        ("Operacionais", previous.man, current.man),
        ("Meios terrestres", previous.terrain, current.terrain),
        ("Meios aéreos", previous.aerial, current.aerial),
        ("Meios aquáticos", previous.aquatic, current.aquatic),
    ):
        if change := _resource_change(label, old, new):
            changes.append(change)

    if previous.severity != current.severity:
        worse = _severity_rank(current) > _severity_rank(previous)
        changes.append(
            Change(
                label="Gravidade",
                old=previous.severity,
                new=current.severity,
                headline="agravamento" if worse else "melhoria",
                escalation=1 if worse else -1,
            )
        )

    return changes


def detect(current: list[Fire], previous: dict[str, Fire], min_severity: str) -> list[Event]:
    """Compare live fires against the last snapshot and produce events.

    `min_severity` gates NEW fires only. Once a fire has been reported, its
    updates and resolution are always reported — going quiet halfway through
    an incident is worse than never having started.
    """
    threshold = SEVERITY_ORDER.index(min_severity)
    events: list[Event] = []
    live_ids = {fire.id for fire in current}

    for fire in current:
        prior = previous.get(fire.id)
        if prior is None:
            if SEVERITY_ORDER.index(fire.severity) >= threshold:
                events.append(Event(kind=NEW, fire=fire))
            continue

        if changes := _diff(prior, fire):
            events.append(Event(kind=UPDATE, fire=fire, previous=prior, changes=changes))

    for fire_id, prior in previous.items():
        if fire_id not in live_ids:
            events.append(Event(kind=RESOLVED, fire=prior, previous=prior))

    # Worst news first, so a burst of emails arrives in a useful order.
    events.sort(key=lambda e: (-SEVERITY_ORDER.index(e.severity), e.kind))
    return events
