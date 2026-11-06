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
