"""Email subject and body composition.

Subjects are front-loaded: severity marker, what happened, where, then the one
number that matters. Mobile notification previews cut off around 40 characters,
so everything a reader needs to triage without opening sits before that.

Bodies are table-based with inline styles — the only layout technique that
survives Gmail, Outlook and Apple Mail intact.
"""

from __future__ import annotations

import html
import time
from dataclasses import dataclass

from changes import NEW, RESOLVED, UPDATE, Event
from config import Config
from fogos import Fire

PALETTE = {
    "major": ("#B42318", "#FEF3F2", "🔴"),
    "elevated": ("#B54708", "#FFFAEB", "🟠"),
    "info": ("#175CD3", "#EFF8FF", "🟡"),
    "resolved": ("#067647", "#ECFDF3", "✅"),
}

EVENT_LABEL = {
    NEW: "Novo incêndio",
    UPDATE: "Atualização",
    RESOLVED: "Ocorrência terminada",
}


@dataclass
class Message:
    subject: str
    html_body: str
    text_body: str


def _duration(started_at: int | None, until: int | None = None) -> str:
    if not started_at:
        return "desconhecida"
    seconds = max(0, int(until or time.time()) - started_at)
    hours, minutes = divmod(seconds // 60, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h{minutes:02d}" if hours else f"{minutes} min"


def _resource_summary(fire: Fire) -> str:
    bits = []
    if fire.man:
        bits.append(f"{fire.man} op")
    if fire.terrain:
        bits.append(f"{fire.terrain} vt")
    if fire.aerial:
        bits.append(f"{fire.aerial} aéreo{'s' if fire.aerial > 1 else ''}")
    return ", ".join(bits)


def _distance_text(fire: Fire) -> str:
    if fire.distance_km is None:
        return "sem coordenadas"
    direction = f" a {fire.bearing}" if fire.bearing else ""
    return f"{fire.distance_km:g} km{direction}"


def build_subject(event: Event) -> str:
    fire = event.fire
    palette_key = "resolved" if event.kind == RESOLVED else event.severity
    icon = PALETTE[palette_key][2]

    if event.kind == NEW:
        tail = _resource_summary(fire) or fire.status
        label = "NOVO INCÊNDIO" if event.severity == "major" else "Novo incêndio"
        return f"{icon} {label} · {fire.place} · {tail}"

    if event.kind == RESOLVED:
        return f"✅ Terminado · {fire.place} · durou {_duration(fire.started_at)}"

    escalating = any(change.escalation > 0 for change in event.changes)
    arrow = "🔺" if escalating else "🔻"
    return f"{arrow} {fire.place} · {event.headline or 'atualização'}"


def _stat_cell(label: str, value: int, accent: str) -> str:
    dim = "" if value else "opacity:0.45;"
    return f"""
      <td width="25%" align="center" style="padding:12px 4px;{dim}">
        <div style="font:700 22px/1.1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{accent};">{value}</div>
        <div style="font:400 11px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#667085;text-transform:uppercase;letter-spacing:.4px;padding-top:4px;">{html.escape(label)}</div>
      </td>"""


def _changes_block(event: Event) -> str:
    if not event.changes:
        return ""

    rows = []
    for change in event.changes:
        arrow_color = "#B42318" if change.escalation > 0 else "#067647" if change.escalation < 0 else "#475467"
        rows.append(
            f"""
        <tr>
          <td style="padding:6px 0;font:600 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#344054;white-space:nowrap;">{html.escape(change.label)}</td>
          <td align="right" style="padding:6px 0;font:400 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#667085;">
            <span style="text-decoration:line-through;">{html.escape(change.old)}</span>
            <span style="color:{arrow_color};padding:0 6px;">→</span>
            <span style="color:{arrow_color};font-weight:700;">{html.escape(change.new)}</span>
          </td>
        </tr>"""
        )

    return f"""
    <tr><td style="padding:0 24px 4px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;border:1px solid #EAECF0;border-radius:10px;">
        <tr><td style="padding:14px 16px 4px;font:700 11px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#667085;text-transform:uppercase;letter-spacing:.6px;">O que mudou</td></tr>
        <tr><td style="padding:0 16px 10px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table>
        </td></tr>
      </table>
    </td></tr>"""


def _detail_rows(fire: Fire, event: Event) -> str:
    until = int(time.time()) if event.kind != RESOLVED else None
    entries = [
        ("Estado", fire.status),
        ("Natureza", fire.natureza or "—"),
        ("Distância", _distance_text(fire)),
        ("Local", fire.detail_location or "—"),
        ("Freguesia", fire.freguesia or "—"),
        ("Concelho", fire.concelho or "—"),
        ("Distrito", fire.district or "—"),
        ("Início", fire.started_display),
        ("Duração", _duration(fire.started_at, until)),
        ("Ocorrência", fire.id),
    ]
    return "".join(
        f"""
        <tr>
          <td width="38%" style="padding:7px 0;border-bottom:1px solid #F2F4F7;font:400 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#667085;">{html.escape(label)}</td>
          <td style="padding:7px 0;border-bottom:1px solid #F2F4F7;font:600 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#101828;">{html.escape(str(value))}</td>
        </tr>"""
        for label, value in entries
    )


def build_html(event: Event, config: Config) -> str:
    fire = event.fire
    palette_key = "resolved" if event.kind == RESOLVED else event.severity
    accent, tint, icon = PALETTE[palette_key]
    label = EVENT_LABEL[event.kind]

    headline = event.headline if event.kind == UPDATE and event.headline else fire.status
    matched = "no raio monitorizado" if fire.matched_by == "radius" else "numa localidade monitorizada"

    return f"""<!DOCTYPE html>
<html lang="pt"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{html.escape(build_subject(event))}</title>
</head>
<body style="margin:0;padding:0;background:#F2F4F7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(fire.full_place)} · {html.escape(headline)} · {html.escape(_distance_text(fire))}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#FFFFFF;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(16,24,40,.1);">

    <tr><td style="background:{accent};padding:18px 24px;">
      <div style="font:700 12px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#FFFFFF;text-transform:uppercase;letter-spacing:1.2px;opacity:.85;">{icon}&nbsp;&nbsp;{html.escape(label)}</div>
      <div style="font:700 24px/1.25 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#FFFFFF;padding-top:6px;">{html.escape(fire.full_place)}</div>
    </td></tr>

    <tr><td style="background:{tint};padding:14px 24px;border-bottom:1px solid #EAECF0;">
      <div style="font:600 15px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{accent};">{html.escape(headline)}</div>
      <div style="font:400 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#475467;padding-top:3px;">{html.escape(_distance_text(fire))} &middot; início {html.escape(fire.started_display)} &middot; {html.escape(fire.natureza or 'natureza desconhecida')}</div>
    </td></tr>

    {_changes_block(event)}

    <tr><td style="padding:8px 20px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #EAECF0;border-radius:10px;">
        <tr>
          {_stat_cell("Operacionais", fire.man, accent)}
          {_stat_cell("Terrestres", fire.terrain, accent)}
          {_stat_cell("Aéreos", fire.aerial, accent)}
          {_stat_cell("Aquáticos", fire.aquatic, accent)}
        </tr>
      </table>
    </td></tr>

    <tr><td style="padding:16px 24px 4px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{_detail_rows(fire, event)}</table>
    </td></tr>

    <tr><td style="padding:20px 24px 24px;">
      <table role="presentation" cellpadding="0" cellspacing="0">
        <tr>
          <td style="border-radius:8px;background:{accent};">
            <a href="{html.escape(fire.detail_url)}" style="display:inline-block;padding:11px 20px;font:600 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#FFFFFF;text-decoration:none;">Ver no fogos.pt</a>
          </td>
          <td width="10"></td>
          <td style="border-radius:8px;border:1px solid #D0D5DD;">
            <a href="{html.escape(fire.map_url)}" style="display:inline-block;padding:10px 20px;font:600 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#344054;text-decoration:none;">Abrir mapa</a>
          </td>
        </tr>
      </table>
    </td></tr>

    <tr><td style="padding:14px 24px;background:#F9FAFB;border-top:1px solid #EAECF0;font:400 11px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#98A2B3;">
      Detetado {html.escape(matched)}. Dados de <a href="https://fogos.pt" style="color:#667085;">fogos.pt</a> / ANEPC, atualizados a cada {config.poll_minutes} min.<br>
      Informação indicativa — em emergência ligue <strong style="color:#667085;">112</strong>.
    </td></tr>

  </table>
</td></tr>
</table>
</body></html>"""


def build_text(event: Event, config: Config) -> str:
    fire = event.fire
    until = int(time.time()) if event.kind != RESOLVED else None

    lines = [
        f"{EVENT_LABEL[event.kind].upper()} — {fire.full_place}",
        "=" * 48,
        f"Estado      : {fire.status}",
        f"Distância   : {_distance_text(fire)}",
        f"Natureza    : {fire.natureza or '—'}",
        f"Local       : {fire.detail_location or '—'}",
        f"Início      : {fire.started_display}  (duração {_duration(fire.started_at, until)})",
        "",
        f"Operacionais: {fire.man}   Terrestres: {fire.terrain}   "
        f"Aéreos: {fire.aerial}   Aquáticos: {fire.aquatic}",
    ]

    if event.changes:
        lines += ["", "O QUE MUDOU", "-" * 48]
        lines += [f"  {c.label}: {c.old} -> {c.new}" for c in event.changes]

    lines += [
        "",
        f"Detalhe: {fire.detail_url}",
        f"Mapa   : {fire.map_url}",
        "",
        f"Ocorrência {fire.id} · dados fogos.pt/ANEPC · ciclo {config.poll_minutes} min",
        "Informação indicativa — em emergência ligue 112.",
    ]
    return "\n".join(lines)


def build_message(event: Event, config: Config) -> Message:
    return Message(
        subject=build_subject(event),
        html_body=build_html(event, config),
        text_body=build_text(event, config),
    )


def build_status_message(config: Config, tracked: list[Fire], title: str, note: str) -> Message:
    """Heartbeat / startup email: proof the watcher is alive and what it sees."""
    active = [fire for fire in tracked if not fire.is_cooling]
    subject = f"📋 {title} · {len(active)} ativo(s), {len(tracked)} em vigilância"

    if tracked:
        rows = "".join(
            f"""
        <tr>
          <td style="padding:8px 0;border-bottom:1px solid #F2F4F7;font:600 13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#101828;">
            {html.escape(fire.full_place)}
            <div style="font-weight:400;color:#667085;padding-top:2px;">{html.escape(fire.status)} · {html.escape(_distance_text(fire))} · {html.escape(_resource_summary(fire) or 'sem meios')}</div>
          </td>
          <td align="right" style="padding:8px 0;border-bottom:1px solid #F2F4F7;">
            <a href="{html.escape(fire.detail_url)}" style="font:600 12px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#175CD3;text-decoration:none;">detalhe</a>
          </td>
        </tr>"""
            for fire in tracked
        )
        table = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
        text_lines = [
            f"- {f.full_place} — {f.status}, {_distance_text(f)}, {_resource_summary(f) or 'sem meios'}"
            for f in tracked
        ]
    else:
        table = '<div style="font:400 14px/1.6 -apple-system,BlinkMacSystemFont,\'Segoe UI\',Arial,sans-serif;color:#475467;">Sem ocorrências na área monitorizada.</div>'
        text_lines = ["Sem ocorrências na área monitorizada."]

    scope = ", ".join(config.locations) or f"raio de {config.max_distance_km:g} km"

    html_body = f"""<!DOCTYPE html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#F2F4F7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#FFFFFF;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(16,24,40,.1);">
    <tr><td style="background:#344054;padding:18px 24px;">
      <div style="font:700 12px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#FFFFFF;text-transform:uppercase;letter-spacing:1.2px;opacity:.75;">FogosPT Alerts</div>
      <div style="font:700 22px/1.25 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#FFFFFF;padding-top:6px;">{html.escape(title)}</div>
    </td></tr>
    <tr><td style="padding:16px 24px 4px;font:400 13px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#475467;">{html.escape(note)}</td></tr>
    <tr><td style="padding:8px 24px 20px;">{table}</td></tr>
    <tr><td style="padding:14px 24px;background:#F9FAFB;border-top:1px solid #EAECF0;font:400 11px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#98A2B3;">
      A monitorizar {html.escape(scope)}, a cada {config.poll_minutes} min. Se estes resumos pararem, o serviço parou.
    </td></tr>
  </table>
</td></tr></table></body></html>"""

    text_body = "\n".join([title, "=" * 48, note, ""] + text_lines + ["", f"Âmbito: {scope}"])
    return Message(subject=subject, html_body=html_body, text_body=text_body)
