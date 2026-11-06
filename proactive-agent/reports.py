"""Subagent results: checked, recorded and reported until the user has heard them.

When a subagent ends, its task is closed in the ledger and a note for the main agent waits there
until that chat's next turn: the evidence to check, or why the task did not finish. After a
restart, the tasks a stopped Hermes left running are reported lost.

A result counts as heard once a turn that showed it to the main agent ends with a reply that no
user message cut into. When the user writes while a result is being reported (Hermes then
redirects or steers the turn, or interrupts it), the result stays unreported: the next tool
result of that turn says so, and if the turn ends without one, the main agent is woken through
Hermes' own message injection to report it; the user's next message carries it otherwise.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import config, ledger, util, workers

logger = logging.getLogger("hermes.plugins.proactive_agent")

WAKE_MARKER = "[proactive-agent] No new message from the user."
_WAKE = (f"{WAKE_MARKER} Your last reply answered the message the user sent while subagent results were "
         "reaching you, so the user has not heard those results yet (listed below). Tell the user now, briefly. "
         "If your last reply already covered them, reply exactly [SILENT].")
_NOTHING_LEFT = ("[proactive-agent] Nothing is left to report: the user already got those results in an "
                 "earlier reply. Reply exactly [SILENT].")
# Each wake answers a user message that cut into a report, so wakes are bounded by the user's own
# messages; the cap only guards against a host that loops.
_MAX_WAKES = 2

_lock = threading.Lock()
# session -> (turn key, interjections already answered by a reminder in that turn)
_reminded: Dict[str, Tuple[str, int]] = {}

# The status values of the default output schema, and other words workers use for the same outcomes.
REPORT_STATUSES = ("completed", "partial", "blocked", "failed")
_DONE_STATUSES = ("completed", "complete", "success", "done")
_FAILED_STATUSES = ("error", "failure", "aborted", "cancelled", "canceled")


def record_dispatch(result: Any) -> None:
    """Keep each task's live transcript path, and whether it runs in the background, from the
    delegate_task dispatch response."""
    data = util.parse_json(result)
    if not isinstance(data, dict):
        return
    ids, paths = data.get("subagent_ids"), data.get("live_transcripts")
    if data.get("status") == "dispatched" and isinstance(ids, list):
        for sid in ids:
            if isinstance(sid, str) and sid:
                ledger.mark_background(sid)
    if isinstance(ids, list) and isinstance(paths, list) and len(ids) == len(paths):
        for sid, path in zip(ids, paths):
            if isinstance(sid, str) and sid and isinstance(path, str) and path:
                ledger.set_transcript(sid, path)


def _report(summary: Any) -> dict:
    """The worker's structured report: a summary that is itself a JSON object with a status or a summary.
    Plain text that merely quotes some JSON is not a report."""
    data = util.json_object(summary)
    return data if data is not None and ("status" in data or "summary" in data) else {}


def _outcome(report: dict, child_status: Any) -> str:
    """How the task ended. Hermes' failed/interrupted wins over a self-reported success, and a status word
    outside the schema still counts as not done rather than falling back to Hermes' "completed"."""
    hermes = str(child_status or "").lower()
    own = str(report.get("status") or "").strip().lower()
    if own in _DONE_STATUSES:
        own = "completed"
    elif own in _FAILED_STATUSES:
        own = "failed"
    elif own and own not in REPORT_STATUSES:
        own = "partial"
    if hermes in ("failed", "interrupted") and own in ("", "completed"):
        return hermes
    return own or (hermes if hermes in ("completed", "failed", "interrupted") else "unknown")


def _strings(value: Any) -> List[str]:
    return [str(v) for v in value if str(v).strip()] if isinstance(value, list) else []


def _written_paths(history: Any) -> List[str]:
    """Paths of successful writes in Hermes' tool_call_history (whose inputs carry only targets)."""
    paths: List[str] = []
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, dict) or item.get("status") == "error" or not workers.is_write(str(item.get("tool_name"))):
            continue
        tool_input = item.get("tool_input")
        targets = tool_input.get("targets") if isinstance(tool_input, dict) else None
        value = targets.get("path") if isinstance(targets, dict) else None
        paths.extend(v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str))
    return paths


def _finished_note(w: dict, final: str, evidence: List[str], duration_ms: Any) -> str:
    minutes = (duration_ms or 0) / 60000.0
    lines = [f"[proactive-agent] Subagent finished: \"{util.short(w['goal'], 80)}\" "
             f"(status: {final}, {len(w['rounds'])} tool rounds, {minutes:.1f} min)."]
    if w["stop_reason"]:
        lines.append(f"- The plugin stopped it: {w['stop_reason']}. Treat it as blocked and decide the next step "
                     "yourself; do not re-dispatch the same plan. Tell the user in one line what got stuck and what "
                     "you will do instead.")
    elif final == ledger.STOPPED:
        changed = (ledger.get(w["id"]) or {}).get("files") or []
        lines.append("- You stopped it (action='stop'). Nothing to report unless the user asks about it."
                     + (f" It had changed: {', '.join(changed[:5])}." if changed else "")
                     + " A follow-up subagent is briefed with its partial work automatically.")
    elif final == "completed":
        if evidence:
            lines.append("- Evidence it reported: " + "; ".join(util.short(e, 120) for e in evidence[:5])
                         + ". Spot-check the real end state before telling the user.")
        else:
            lines.append("- It claims success without evidence. Verify the real end state before telling the user.")
    else:
        lines.append("- It did not finish. Read its blockers and tell the user plainly what is left. A follow-up "
                     "subagent is briefed with this task's record automatically.")
    return "\n".join(lines)


def on_subagent_stop(**kw: Any) -> None:
    child_session = str(kw.get("child_session_id") or "")
    w = workers.finish(child_session)
    report = _report(kw.get("child_summary"))
    outcome = _outcome(report, kw.get("child_status"))
    summary = str(report.get("summary") or kw.get("child_summary") or "")
    evidence, blockers = _strings(report.get("evidence")), _strings(report.get("blockers"))
    files = _written_paths(kw.get("tool_call_history"))

    if w is None:
        # The chat was reset or the plugin reloaded mid-run: close the record, no note to deliver.
        known = ledger.by_child(child_session)
        if known and known["status"] in (ledger.RUNNING, ledger.STOPPED, ledger.LOST):
            final = ledger.STOPPED if known["status"] == ledger.STOPPED else outcome
            ledger.finish(known["id"], final, summary, evidence, blockers, files)
        return

    if w["stop_reason"]:
        final = "blocked"
    elif w["stopped_by_parent"]:
        final = ledger.STOPPED
    else:
        final = outcome
    # Only a background result arrives in a turn of its own that a user message can cut into; an
    # inline one is the delegate_task tool result. A task the main agent stopped itself is not news.
    background = bool((ledger.get(w["id"]) or {}).get("background"))
    ledger.finish(w["id"], final, summary, evidence, blockers, files,
                  unreported=background and final != ledger.STOPPED)
    ledger.add_note(w["parent_session"], _finished_note(w, final, evidence, kw.get("duration_ms")))


def _lost_note(task: dict) -> str:
    files = task.get("files") or []
    changed = f" It had changed: {', '.join(files[:5])}." if files else ""
    return (f"[proactive-agent] Hermes restarted while a subagent was running: \"{util.short(task.get('goal'), 80)}\" "
            f"(started {ledger.minutes_since(task.get('started'))} min ago). Its result is lost and its side effects "
            f"are unknown.{changed} Check the real state before re-dispatching or telling the user it is done.")


def recover_lost() -> None:
    """Open the ledger and tell each chat which of its subagents a previous Hermes process left running."""
    for task in ledger.load():
        ledger.mark_unreported(task["id"])
        ledger.add_note(task["parent"], _lost_note(task))
        logger.warning("proactive-agent: subagent %s was running when Hermes stopped; marked lost", task["id"])


# ---------------------------------------------------------------- results the user has not heard

def _unreported_note(tasks: List[dict]) -> str:
    rows = []
    for t in tasks:
        summary = util.short(t.get("summary"), 200)
        rows.append(f"- \"{util.short(t.get('goal'), 80)}\" ended {t.get('status')} "
                    f"{ledger.minutes_since(t.get('ended'))} min ago" + (f": {summary}" if summary else "."))
    return ("[proactive-agent] The user has not heard these subagent results yet: the turn that received them "
            "was cut short or answered another message.\n" + "\n".join(rows) + "\nTheir full results are earlier in this chat. "
            "Answer the user's latest message and, in the same reply, tell the user these results: what was done, "
            "what is left.")


def is_wake(user_message: Any) -> bool:
    return isinstance(user_message, str) and WAKE_MARKER in user_message


def pending_notes(parent_session: str, turn: str, user_message: Any = None) -> str:
    """Notes for the start of a main-agent turn: new results first, then those the user still has not heard."""
    notes = ledger.pop_notes(parent_session)
    shown = ledger.show_unreported(parent_session, turn)
    again = [t for t in shown if t["seen_before"]]
    if again:
        notes.append(_unreported_note(again))
    elif is_wake(user_message) and not shown:
        notes.append(_NOTHING_LEFT)
    return "\n\n".join(notes)


def _answered(session_id: str, turn: str) -> int:
    seen_turn, count = _reminded.get(session_id, ("", 0))
    return count if seen_turn == turn else 0


def tool_result_note(session_id: str, turn: Optional[dict], result: Any) -> Optional[str]:
    """After the user wrote into a turn that is reporting results, say so on the next tool result."""
    if not turn:
        return None
    with _lock:
        if turn["interjections"] <= _answered(session_id, turn["key"]):
            return None
        _reminded[session_id] = (turn["key"], turn["interjections"])
    tasks = ledger.unreported_in_turn(session_id, turn["key"])
    return util.append_note(result, _unreported_note(tasks)) if tasks else None


def _wake(session_key: str) -> bool:
    ctx = config.host()
    inject = getattr(ctx, "inject_message", None)
    if not session_key or not callable(inject):
        return False
    try:
        # An internal turn: Hermes queues it behind a running turn and never lets it interrupt one.
        return bool(inject(_WAKE, session_key=session_key))
    except Exception as e:
        logger.warning("proactive-agent: could not wake the main agent: %s", e)
        return False


def on_turn_end(session_id: str, turn: Optional[dict], completed: bool) -> None:
    """Settle the results this turn reported, or keep them for a wake or the next turn."""
    if not turn:
        return
    with _lock:
        answered = _answered(session_id, turn["key"])
        _reminded.pop(session_id, None)
    tasks = ledger.unreported_in_turn(session_id, turn["key"])
    if not tasks or not completed:
        # An interrupted or failed turn: the user's next message (Hermes runs it next) repeats them.
        return
    if turn["interjections"] <= answered:
        ledger.settle(t["id"] for t in tasks)
        return
    due = [t["id"] for t in tasks if int(t.get("wakes") or 0) < _MAX_WAKES]
    if due and _wake(turn.get("session_key") or ""):
        ledger.add_wake(due)
        logger.info("proactive-agent: woke %s to report %d result(s) the user has not heard", session_id, len(due))


def forget(session_id: str) -> None:
    with _lock:
        _reminded.pop(session_id, None)


def reset_all() -> None:
    with _lock:
        _reminded.clear()
