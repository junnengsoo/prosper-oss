from __future__ import annotations

import json
from html import escape
from textwrap import shorten
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database.models import Contact, Conversation, Message, StageRun


def badge(value: object, tone: str = "neutral") -> str:
    text = escape(str(value if value is not None else "-"))
    return f'<span class="badge {tone}">{text}</span>'


def preview(value: str | None, width: int = 160) -> str:
    if not value:
        return "-"
    return escape(shorten(value.replace("\n", " "), width=width, placeholder="..."))


def json_preview(value: str | None, width: int = 220) -> str:
    if not value:
        return "-"
    try:
        rendered = json.dumps(json.loads(value), ensure_ascii=False)
    except json.JSONDecodeError:
        rendered = value
    return preview(rendered, width)


def status_tone(value: object) -> str:
    normalized = str(value or "").lower()
    if normalized in {"open", "active", "available", "sent", "success"}:
        return "good"
    if normalized in {"connecting", "pending"}:
        return "warn"
    if normalized in {"close", "closed", "failed", "stale", "manual_review", "paused", "ignored"}:
        return "bad"
    return "neutral"


def latest_message(session: Session, conversation_id: int) -> Message | None:
    return session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.timestamp_ms.desc(), Message.id.desc())
    )


def render_runtime_cards(runtime: dict[str, Any]) -> str:
    config = runtime.get("config", {})
    bridge = runtime.get("bridge", {})
    llm = runtime.get("llm", {})

    cards = [
        ("AI", "Paused" if config.get("pause_ai") == "true" else "Running", "bad" if config.get("pause_ai") == "true" else "good"),
        ("Sending", "Locked" if config.get("send_lock") == "true" else "Open", "bad" if config.get("send_lock") == "true" else "good"),
        ("WhatsApp", bridge.get("connection", "offline"), status_tone(bridge.get("connection"))),
        ("DeepSeek", "Ready" if llm.get("configured") else "Missing", "good" if llm.get("configured") else "bad"),
    ]
    html = ['<section class="cards">']
    for label, value, tone in cards:
        html.append(
            '<article class="metric">'
            f'<div class="metric-label">{escape(label)}</div>'
            f'<div class="metric-value">{badge(value, tone)}</div>'
            "</article>"
        )
    html.append("</section>")
    return "\n".join(html)


def render_conversations(session: Session) -> str:
    conversations = session.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(12)).all()
    rows = []
    for conversation in conversations:
        contact = session.get(Contact, conversation.contact_id)
        message = latest_message(session, conversation.id)
        rows.append(
            "<tr>"
            f'<td><a href="/demo/conversations/{conversation.id}">#{conversation.id}</a></td>'
            f"<td>{escape(contact.display_name or contact.chat_jid if contact else '-')}</td>"
            f"<td>{badge(conversation.current_stage or '-', status_tone(conversation.current_stage))}</td>"
            f"<td>{escape(conversation.matched_property_id or '-')}</td>"
            f"<td>{escape(message.direction if message else '-')}</td>"
            f"<td>{preview(message.text if message else None)}</td>"
            f"<td>{escape(str(conversation.updated_at))}</td>"
            "<td>"
            + (
                f'<form method="post" action="/demo/conversations/{conversation.id}/close">'
                '<button type="submit" class="button small">Close</button>'
                "</form>"
                if conversation.status != "closed"
                else badge("closed", "bad")
            )
            + "</td>"
            "</tr>"
        )
    body = "\n".join(rows) or '<tr><td colspan="9" class="empty">No conversations yet.</td></tr>'
    return (
        '<section class="panel"><h2>Recent Conversations</h2><table>'
        "<thead><tr><th>ID</th><th>Contact</th><th>Stage</th><th>Host</th><th>Current</th><th>Latest</th><th>Preview</th><th>Updated</th><th>Action</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def render_paused_contacts(session: Session) -> str:
    contacts = session.scalars(
        select(Contact)
        .where(Contact.status.in_(["paused", "ignored"]))
        .order_by(Contact.last_message_at.desc().nullslast(), Contact.updated_at.desc())
        .limit(12)
    ).all()
    rows = []
    for contact in contacts:
        rows.append(
            "<tr>"
            f"<td>#{contact.id}</td>"
            f"<td>{escape(contact.display_name or contact.chat_jid)}</td>"
            f"<td>{badge(contact.status, status_tone(contact.status))}</td>"
            f"<td>{preview(contact.status_reason, 100)}</td>"
            f"<td>{escape(str(contact.last_message_at or '-'))}</td>"
            f"<td>{escape(str(contact.updated_at))}</td>"
            "<td>"
            + (
                f'<form method="post" action="/demo/contacts/{contact.id}/unpause">'
                '<button type="submit" class="button small">Unpause</button>'
                "</form>"
                if contact.status == "paused"
                else badge("ignored", "bad")
            )
            + "</td>"
            "</tr>"
        )
    body = "\n".join(rows) or '<tr><td colspan="7" class="empty">No paused or ignored contacts.</td></tr>'
    return (
        '<section class="panel"><h2>Paused / Ignored Contacts</h2><table>'
        "<thead><tr><th>ID</th><th>Contact</th><th>Status</th><th>Reason</th><th>Last message</th><th>Updated</th><th>Action</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def render_stage_runs(session: Session, conversation_id: int | None = None) -> str:
    query = select(StageRun).order_by(StageRun.created_at.desc(), StageRun.id.desc()).limit(12)
    if conversation_id is not None:
        query = (
            select(StageRun)
            .where(StageRun.conversation_id == conversation_id)
            .order_by(StageRun.created_at.desc(), StageRun.id.desc())
            .limit(20)
        )
    runs = session.scalars(query).all()
    rows = []
    for run in runs:
        rows.append(
            "<tr>"
            f"<td>#{run.id}</td>"
            f"<td>{escape(str(run.conversation_id or '-'))}</td>"
            f"<td>{badge(run.stage, status_tone(run.stage))}</td>"
            f"<td>{badge(run.status, status_tone(run.status))}</td>"
            f"<td>{json_preview(run.output_json)}</td>"
            f"<td>{preview(run.error, 100)}</td>"
            f"<td>{escape(str(run.created_at))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or '<tr><td colspan="7" class="empty">No stage runs yet.</td></tr>'
    return (
        '<section class="panel"><h2>AI Stage Runs</h2><table>'
        "<thead><tr><th>Run</th><th>Conversation</th><th>Stage</th><th>Status</th><th>Output</th><th>Error</th><th>Time</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def render_messages(session: Session, conversation_id: int) -> str:
    messages = session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.timestamp_ms, Message.id)
    ).all()
    items = []
    for message in messages:
        items.append(
            f'<div class="bubble {escape(message.direction)}">'
            f'<div class="bubble-meta">{escape(message.direction)} · {escape(str(message.timestamp_ms))}</div>'
            f'<div>{escape(message.text)}</div>'
            "</div>"
        )
    body = "\n".join(items) or '<div class="empty">No messages.</div>'
    return f'<section class="panel"><h2>Messages</h2><div class="thread">{body}</div></section>'


def page_shell(title: str, body: str, refresh: bool = True) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="5">' if refresh else ""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_tag}
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --bg: #f4f6f8; --panel: #fff; --border: #dbe3ec; --text: #172033; --muted: #6b7788; --green: #16794c; --red: #b42318; --amber: #a15c07; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; }}
    header {{ position: sticky; top: 0; z-index: 2; display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 14px 20px; background: rgba(255,255,255,.94); border-bottom: 1px solid var(--border); backdrop-filter: blur(8px); }}
    h1 {{ font-size: 20px; margin: 0; }}
    h2 {{ font-size: 15px; margin: 0 0 12px; }}
    main {{ padding: 18px 20px 28px; max-width: 1500px; margin: 0 auto; }}
    a {{ color: #155eef; text-decoration: none; }}
    .links {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .cards {{ display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr)); gap: 10px; margin-bottom: 12px; }}
    .cards.compact {{ grid-template-columns: repeat(4, minmax(150px, 1fr)); }}
    .metric, .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; }}
    .metric {{ padding: 12px; }}
    .metric-label, .muted {{ color: var(--muted); font-size: 12px; }}
    .metric-value {{ margin-top: 8px; }}
    .count {{ font-size: 24px; font-weight: 700; margin: 4px 0; }}
    .panel {{ padding: 14px; margin-bottom: 12px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 920px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #edf1f5; padding: 9px 8px; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 600; }}
    .badge {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 650; background: #edf1f5; color: #344054; white-space: nowrap; }}
    .badge.good {{ background: #dcfae6; color: var(--green); }}
    .badge.warn {{ background: #fef0c7; color: var(--amber); }}
    .badge.bad {{ background: #fee4e2; color: var(--red); }}
    .empty {{ color: var(--muted); padding: 18px; text-align: center; }}
    .thread {{ display: grid; gap: 10px; max-width: 880px; }}
    .bubble {{ padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border); background: #fff; white-space: pre-wrap; }}
    .bubble.inbound {{ justify-self: start; max-width: 72%; }}
    .bubble.outbound, .bubble.human {{ justify-self: end; max-width: 72%; background: #e8f7ef; border-color: #b7e4cb; }}
    .bubble-meta {{ color: var(--muted); font-size: 11px; margin-bottom: 4px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
    .button {{ appearance: none; border: 1px solid var(--border); background: #fff; color: var(--text); border-radius: 6px; padding: 8px 11px; font: inherit; font-weight: 650; cursor: pointer; }}
    .button:hover {{ border-color: #aebdcc; background: #f8fafc; }}
    .button.small {{ padding: 5px 8px; font-size: 12px; }}
    @media (max-width: 900px) {{ .cards, .cards.compact {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} header {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>{escape(title)}</h1><div class="muted">Demo monitor · send lock is the safety gate · auto-refreshes every 5s</div></div>
    <nav class="links"><a href="/">Overview</a><a href="/api/runtime/status">Runtime JSON</a><a href="/docs">API docs</a></nav>
  </header>
  <main>{body}</main>
</body>
</html>"""


async def render_demo_overview(session: Session, runtime: dict[str, Any]) -> str:
    body = "\n".join(
        [
            render_runtime_cards(runtime),
            render_conversations(session),
            render_paused_contacts(session),
            render_stage_runs(session),
        ]
    )
    return page_shell("Prosper Demo Monitor", body)


async def render_demo_conversation(session: Session, runtime: dict[str, Any], conversation_id: int) -> str:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return page_shell("Conversation Not Found", '<section class="panel">Conversation not found.</section>', refresh=False)
    contact = session.get(Contact, conversation.contact_id)
    title = f"Conversation #{conversation.id}"
    summary = (
        '<section class="panel">'
        f"<h2>{escape(contact.display_name or contact.chat_jid if contact else title)}</h2>"
        f"<p>{badge(conversation.current_stage or '-', status_tone(conversation.current_stage))} "
        f"Current: {escape(conversation.matched_property_id or '-')} "
        f"</p>"
        '<div class="actions">'
        + (
            f'<form method="post" action="/demo/conversations/{conversation.id}/close">'
            '<button type="submit" class="button">Close conversation</button>'
            "</form>"
            if conversation.status != "closed"
            else str(badge("closed", "bad"))
        )
        + '<a class="button" href="/">Back to overview</a>'
        + "</div>"
        "</section>"
    )
    body = "\n".join(
        [
            render_runtime_cards(runtime),
            summary,
            render_messages(session, conversation_id),
            render_stage_runs(session, conversation_id),
        ]
    )
    return page_shell(title, body)
