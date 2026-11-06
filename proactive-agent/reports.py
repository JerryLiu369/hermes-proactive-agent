"""Subagent results: checked, recorded and reported to the main agent.

When a subagent ends, its task is closed in the ledger and a note for the main agent waits there
until that chat's next turn: the evidence to check, or why the task did not finish. After a
restart, the tasks a stopped Hermes left running are reported lost.
"""

from __future__ import annotations

import logging
from typing import Any, List

from . import ledger, util, workers

logger = logging.getLogger("hermes.plugins.proactive_agent")

# The status values of the default output schema, and other words workers use for the same outcomes.
REPORT_STATUSES = ("completed", "partial", "blocked", "failed")
_DONE_STATUSES = ("completed", "complete", "success", "done")
_FAILED_STATUSES = ("error", "failure", "aborted", "cancelled", "canceled")


def record_dispatch(result: Any) -> None:
    """Keep each task's live transcript path from the delegate_task dispatch response."""
    data = util.parse_json(result)
    if not isinstance(data, dict):
        return
    ids, paths = data.get("subagent_ids"), data.get("live_transcripts")
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
    ledger.finish(w["id"], final, summary, evidence, blockers, files)
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
        ledger.add_note(task["parent"], _lost_note(task))
        logger.warning("proactive-agent: subagent %s was running when Hermes stopped; marked lost", task["id"])


def pending_notes(parent_session: str) -> str:
    return "\n\n".join(ledger.pop_notes(parent_session))
