"""Reports: result notes for the main agent."""

import json

from helpers import spawn_worker, start_turn


def _stop(plugin, summary, status="completed"):
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status=status,
                          child_summary=summary, tool_call_history=[], duration_ms=120000)


def test_result_without_evidence_asks_for_verification(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, "All fixed, 143 nodes OK")
    injected = start_turn(plugin, turn="t2")
    assert "without evidence" in injected["context"]
    assert start_turn(plugin, turn="t3") is None, "notes are delivered once"


def test_result_with_evidence_lists_it(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, json.dumps({"status": "completed", "summary": "ok",
                              "evidence": ["curl -sI https://app.example.com/ping -> HTTP/2 200"]}))
    injected = start_turn(plugin, turn="t2")
    assert "HTTP/2 200" in injected["context"] and "Spot-check" in injected["context"]


def test_stopped_or_unfinished_results(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    worker["stop_reason"] = "ran the same step 5 times"
    _stop(plugin, "done")
    assert "The plugin stopped it" in start_turn(plugin, turn="t2")["context"]
    spawn_worker(plugin, sub_id="sa-1-def", child_session="child-2")
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-2", child_status="failed",
                          child_summary="no access", duration_ms=1000)
    assert "did not finish" in start_turn(plugin, turn="t3")["context"]


def test_turn_end_keeps_workers_and_notes(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, "done")
    plugin._on_turn_end(session_id="s1", completed=True)
    assert "Subagent finished" in start_turn(plugin, turn="t2")["context"]


def test_session_reset_clears_everything(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, "done")
    plugin._on_session_clean(session_id="s1")
    assert plugin.workers.lookup(task_id="sa-0-abc") is None
    assert "s1" not in plugin.secretary._turns and plugin.secretary.user_words("s1") == []
    assert plugin.context.platform_for(session_id="s1") == ""
    assert "Subagent finished" in start_turn(plugin, turn="t2")["context"], "pending notes outlive the reset"
