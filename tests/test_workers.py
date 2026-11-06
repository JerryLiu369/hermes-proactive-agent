"""Workers: repeat stop, result notes and the stuck alert."""

import json
import threading
from types import SimpleNamespace

from helpers import main_tool, spawn_worker, start_turn, w_note, w_post, w_tool


def test_worker_only_governed_under_governed_parent(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, session="cli-s", platform="cli")
    plugin._subagent_start(parent_session_id="cli-s", child_session_id="c9", child_subagent_id="sa-9", child_goal="g")
    assert plugin.workers.lookup(task_id="sa-9") is None


def test_worker_reminded_then_stopped_on_repeated_step(plugin, make_ctx):
    posted = []
    alerted = threading.Event()

    def post(text):
        posted.append(text)
        alerted.set()
        return SimpleNamespace(ok=True)

    make_ctx(post_message=post)
    start_turn(plugin, user="又 522 了")
    worker = spawn_worker(plugin)
    curl = {"command": "curl -sI https://app.example.com/ping"}
    for i in range(5):
        assert w_tool(plugin, "terminal", curl, api=f"r{i}") is None
        result = json.dumps({"output": f"HTTP/2 522 \ndate: Wed, 04 Nov 2026 12:24:3{i} GMT", "exit_code": 0})
        w_post(plugin, "terminal", curl, result, api=f"r{i}")
        note = w_note(plugin)
        if i == 2:
            assert "same step 3 times" in note and "at 5 repeats" in note
        elif i < 4:
            assert note is None
    assert worker["stop_reason"] and "HTTP/2 522" in worker["stop_reason"]
    assert "STOP" in note
    blocked = w_tool(plugin, "terminal", {"command": "ls"}, api="r9")
    assert blocked["action"] == "block" and "final report" in blocked["message"]
    assert alerted.wait(2) and posted == ["后台子任务卡住了，已叫停：Fix the 522 on app.example.com。主助手会接着处理。"]


def test_different_steps_failing_alike_are_not_repeats(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    for i in range(8):
        w_post(plugin, "terminal", {"command": f"grep -q pattern{i} /srv/app/x.toml"},
               json.dumps({"output": "", "exit_code": 1}), api=f"r{i}")
    assert worker["stop_reason"] is None


def test_step_budget_is_left_to_hermes(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    for i in range(200):
        assert w_tool(plugin, "read_file", {"path": f"/tmp/f{i}"}, api=f"r{i}") is None
        assert w_note(plugin) is None


def test_worker_commands_are_left_to_hermes(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    for command in ("sudo iptables -L", "cat /etc/ssh/sshd_config", "grep reboot /var/log/syslog"):
        assert w_tool(plugin, "terminal", {"command": command}) is None


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


def test_running_workers_are_not_injected_each_turn(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert start_turn(plugin, turn="t2") is None
    assert plugin.workers.running_tasks("s1")
    assert main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"}, turn="t2") is None
    assert not plugin.workers.running_tasks("s1"), "stopped workers are no longer listed as running"


def test_no_periodic_chat_messages(plugin, make_ctx):
    posted = []
    make_ctx(post_message=lambda text: posted.append(text) or SimpleNamespace(ok=True))
    start_turn(plugin, user="帮我部署一下")
    before = threading.active_count()
    spawn_worker(plugin)
    w_post(plugin, "terminal", {"command": "make deploy"}, json.dumps({"output": "ok", "exit_code": 0}))
    assert threading.active_count() == before, "spawning a worker starts no timer"
    assert posted == []
