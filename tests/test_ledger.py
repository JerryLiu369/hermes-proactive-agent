"""Task ledger: persistent records shared by the main agent and its workers."""

import json
import sqlite3

from helpers import GOOD_CONTEXT, main_tool, spawn_worker, start_turn, w_post

WRANGLER = "/srv/app/wrangler.toml"


def _dispatched(plugin, ids, paths):
    result = json.dumps({"status": "dispatched", "subagent_ids": ids, "live_transcripts": paths})
    plugin._post_tool_call("delegate_task", {}, result, session_id="s1", turn_id="t1", task_id="main")


def _stop(plugin, summary, child="child-1", status="completed", history=None):
    plugin._subagent_stop(parent_session_id="s1", child_session_id=child, child_status=status,
                          child_summary=summary, tool_call_history=history or [], duration_ms=60000)


def test_ledger_records_a_task_from_start_to_end(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _dispatched(plugin, ["sa-0-abc"], ["/home/u/.hermes/cache/delegation/live/d1/task-0.log"])
    w_post(plugin, "write_file", {"path": WRANGLER, "content": "x"}, '{"ok": true}')
    w_post(plugin, "read_file", {"path": "/srv/app/README.md"}, "text")
    main_tool(plugin, "delegate_task", {"action": "steer", "subagent_id": "sa-0-abc", "message": "keep the old route"})
    task = plugin.ledger.get("sa-0-abc")
    assert task["status"] == "running" and task["parent"] == "s1"
    assert task["files"] == [WRANGLER] and task["steers"] == ["keep the old route"]
    assert task["transcript"].endswith("task-0.log")

    history = [{"tool_name": "patch", "tool_input": {"targets": {"path": "/srv/app/routes.ts"}}},
               {"tool_name": "read_file", "tool_input": {"targets": {"path": "/etc/hosts"}}}]
    _stop(plugin, json.dumps({"status": "blocked", "summary": "route needs a DNS change",
                              "blockers": ["no Cloudflare token"]}), history=history)
    task = plugin.ledger.get("sa-0-abc")
    assert task["status"] == "blocked" and task["blockers"] == ["no Cloudflare token"]
    assert task["files"] == [WRANGLER, "/srv/app/routes.ts"]


def test_unfinished_task_briefs_the_follow_up(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _dispatched(plugin, ["sa-0-abc"], ["/logs/task-0.log"])
    _stop(plugin, json.dumps({"status": "blocked", "summary": "found the bad route", "blockers": ["no token"]}))
    note = start_turn(plugin, turn="t2")["context"]
    assert "did not finish" in note and "briefed with this task's record" in note

    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Finish the 522 fix", "context": GOOD_CONTEXT}]},
                    turn="t2")
    briefing = res["args"]["tasks"][0]["context"]
    assert "Earlier unfinished work in this chat" in briefing
    assert "found the bad route" in briefing and "no token" in briefing and "/logs/task-0.log" in briefing


def test_completed_tasks_stay_out_of_the_briefing(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, json.dumps({"status": "completed", "summary": "done", "evidence": ["HTTP/2 200"]}))
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Next", "context": GOOD_CONTEXT}]}, turn="t2")
    assert "Earlier unfinished work" not in res["args"]["tasks"][0]["context"]


def test_brief_naming_a_file_another_worker_changes_is_returned_once(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    w_post(plugin, "patch", {"path": WRANGLER}, '{"ok": true}')
    task = {"goal": "Tune caching", "context": GOOD_CONTEXT}
    first = main_tool(plugin, "delegate_task", {"tasks": [task]})
    assert first["action"] == "block" and "already changing /srv/app/wrangler.toml" in first["message"]
    second = main_tool(plugin, "delegate_task", {"tasks": [task]})
    assert second["action"] == "modify"
    briefing = second["args"]["tasks"][0]["context"]
    assert "Other subagents running now" in briefing and "changing /srv/app/wrangler.toml" in briefing


def test_clash_matches_whole_paths_only(plugin):
    mentions = plugin.handoff._mentions
    assert mentions("edit app/wrangler.toml please", WRANGLER)
    assert mentions("see C:\\srv\\app\\wrangler.toml", "C:/srv/app/wrangler.toml")
    assert not mentions("edit myapp/wrangler.toml", WRANGLER)
    assert not mentions("edit /srv/app/wrangler.tomlx", WRANGLER)
    assert not mentions("data.py", "a.py")


def test_unrelated_brief_is_not_returned(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    w_post(plugin, "write_file", {"path": "/srv/other/config.yaml"}, '{"ok": true}')
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix", "context": GOOD_CONTEXT}]})
    assert res["action"] == "modify"


def test_restart_marks_running_tasks_lost_and_tells_the_main_agent(plugin, make_ctx, tmp_path, monkeypatch):
    path = tmp_path / "restart.db"
    plugin.ledger.load(path)
    plugin.ledger.start("sa-old", "s1", "Migrate the database", "child-old")
    plugin.ledger.add_files("sa-old", ["/srv/db/schema.sql"])
    plugin.ledger.start("sa-live", "s1", "Still running here", "child-live")
    db = sqlite3.connect(str(path))
    db.execute("UPDATE tasks SET pid = 0 WHERE id = 'sa-old'")
    db.commit()
    db.close()

    monkeypatch.setattr(plugin.ledger, "_open_default", lambda: sqlite3.connect(str(path), check_same_thread=False))
    make_ctx()
    assert plugin.ledger.get("sa-old")["status"] == "lost"
    assert plugin.ledger.get("sa-live")["status"] == "running", "this process's own tasks are never marked lost"
    note = start_turn(plugin)["context"]
    assert "Hermes restarted" in note and "Migrate the database" in note and "/srv/db/schema.sql" in note

    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Retry", "context": GOOD_CONTEXT}]})
    assert "ended lost" in res["args"]["tasks"][0]["context"]


def test_ledger_survives_a_reload(plugin, tmp_path):
    path = tmp_path / "keep.db"
    plugin.ledger.load(path)
    plugin.ledger.start("sa-1", "s1", "goal", "c1")
    plugin.ledger.finish("sa-1", "partial", "half done")
    plugin.ledger.load(path)
    assert plugin.ledger.get("sa-1")["summary"] == "half done"


def test_new_session_cleans_the_old_one_and_keeps_the_ledger(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    plugin._on_session_clean(session_id="")
    assert plugin.workers.lookup(task_id="sa-0-abc") is not None, "an empty session id clears nothing"
    plugin._on_session_clean(session_id="s2", old_session_id="s1")
    assert plugin.workers.lookup(task_id="sa-0-abc") is None
    assert plugin.ledger.get("sa-0-abc")["status"] == "running"
    _stop(plugin, json.dumps({"status": "failed", "summary": "interrupted"}), status="interrupted")
    assert plugin.ledger.get("sa-0-abc")["status"] == "failed", "the record is closed without a note"
    assert start_turn(plugin, session="s1", turn="t2") is None


def test_restart_note_waits_in_the_ledger_for_the_chat(plugin, make_ctx, tmp_path, monkeypatch):
    """A CLI process (or a second restart) that marks the task lost must not swallow the note."""
    path = tmp_path / "shared.db"
    plugin.ledger.load(path)
    plugin.ledger.start("sa-old", "s1", "Rotate the TLS certificate", "child-old")
    db = sqlite3.connect(str(path))
    db.execute("UPDATE tasks SET pid = 0 WHERE id = 'sa-old'")
    db.commit()
    db.close()
    monkeypatch.setattr(plugin.ledger, "_open_default", lambda: sqlite3.connect(str(path), check_same_thread=False))
    make_ctx()  # the first process to load marks the task lost and queues the note
    plugin._reset_all()
    make_ctx()  # the gateway loads later: nothing left to mark, the note is still waiting
    note = start_turn(plugin)["context"]
    assert "Hermes restarted" in note and "Rotate the TLS certificate" in note
    assert start_turn(plugin, turn="t2") is None, "the note is delivered once"


def test_result_note_survives_a_reload_before_the_next_turn(plugin, make_ctx, tmp_path):
    path = tmp_path / "notes.db"
    plugin.ledger.load(path)
    make_ctx()
    plugin.ledger.load(path)
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, json.dumps({"status": "completed", "summary": "ok", "evidence": ["HTTP/2 200"]}))
    plugin._reset_all()
    plugin.ledger.load(path)
    assert "HTTP/2 200" in start_turn(plugin, turn="t2")["context"]


def test_stop_by_the_main_agent_is_recorded_at_once(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    w_post(plugin, "write_file", {"path": WRANGLER}, '{"ok": true}')
    main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"})
    assert plugin.ledger.get("sa-0-abc")["status"] == "stopped"
    assert plugin.workers.running_tasks("s1") == []
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "New plan", "context": GOOD_CONTEXT}]})
    briefing = res["args"]["tasks"][0]["context"]
    assert "ended stopped" in briefing and WRANGLER in briefing, "the next worker still sees the partial work"

    _stop(plugin, "Interrupted by the parent", status="interrupted")
    note = start_turn(plugin, turn="t2")["context"]
    assert "You stopped it" in note and "did not finish" not in note
    assert plugin.ledger.get("sa-0-abc")["status"] == "stopped"


def test_stop_of_another_chats_task_is_ignored(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    start_turn(plugin, session="s9")
    main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"}, session="s9")
    assert plugin.ledger.get("sa-0-abc")["status"] == "running"


def test_status_comes_from_the_report_not_from_quoted_json(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, 'Checked the service, it answered {"status": "running", "uptime": 5}. All good.')
    assert plugin.ledger.get("sa-0-abc")["status"] == "completed"
    assert plugin.workers.running_tasks("s1") == []

    spawn_worker(plugin, sub_id="sa-1", child_session="child-2")
    _stop(plugin, json.dumps({"status": "completed", "summary": "done"}), child="child-2", status="failed")
    assert plugin.ledger.get("sa-1")["status"] == "failed", "Hermes' failure wins over a self-reported success"

    spawn_worker(plugin, sub_id="sa-2", child_session="child-3")
    _stop(plugin, json.dumps({"status": "running", "summary": "still going"}), child="child-3")
    assert plugin.ledger.get("sa-2")["status"] == "completed", "a finished task is never recorded as running"


def test_closing_after_reset_normalises_the_status(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    plugin._on_session_clean(session_id="s2", old_session_id="s1")
    _stop(plugin, json.dumps({"status": "done", "summary": "ok"}))
    assert plugin.ledger.get("sa-0-abc")["status"] == "completed"


def test_failed_writes_are_not_recorded(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    w_post(plugin, "write_file", {"path": "/srv/a.txt"}, '{"error": "file changed since it was read"}')
    w_post(plugin, "patch", {"path": "/srv/b.txt"}, '{"success": false, "error": "no match"}')
    assert plugin.ledger.get("sa-0-abc")["files"] == []
    history = [{"tool_name": "write_file", "tool_input": {"targets": {"path": "/srv/c.txt"}}, "status": "error"},
               {"tool_name": "write_file", "tool_input": {"targets": {"path": "/srv/d.txt"}}, "status": "ok"}]
    _stop(plugin, "done", history=history)
    assert plugin.ledger.get("sa-0-abc")["files"] == ["/srv/d.txt"]


def test_clash_path_edge_cases(plugin):
    mentions = plugin.handoff._mentions
    assert mentions("edit /srv/app/wrangler.toml", "app/wrangler.toml"), "a relative record matches a full path"
    assert mentions("edit ./app/wrangler.toml", "./app/wrangler.toml")
    assert mentions("then edit /srv/app/wrangler.toml.", WRANGLER), "a sentence-ending period still matches"
    assert not mentions("restore /srv/app/wrangler.toml.bak", WRANGLER)
    assert not mentions("copy /srv/app/.env.example", "/srv/app/.env")


def test_running_records_expire_like_the_worker_registry(plugin, make_ctx, tmp_path):
    path = tmp_path / "stale.db"
    plugin.ledger.load(path)
    plugin.ledger.start("sa-stale", "s1", "Never reported back", "c-stale")
    plugin.ledger._conn().execute("UPDATE tasks SET started = started - 7 * 3600 WHERE id = 'sa-stale'")
    plugin.ledger._conn().commit()
    assert plugin.workers.running_tasks("s1") == []


def test_thin_brief_is_returned_again_next_turn_without_turn_ids(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, turn="")
    thin = {"tasks": [{"goal": "Fix", "context": "short"}]}
    assert main_tool(plugin, "delegate_task", thin, turn="")["action"] == "block"
    assert main_tool(plugin, "delegate_task", thin, turn="")["action"] == "modify"
    plugin._on_turn_end(session_id="s1")
    start_turn(plugin, turn="")
    assert main_tool(plugin, "delegate_task", thin, turn="")["action"] == "block"
