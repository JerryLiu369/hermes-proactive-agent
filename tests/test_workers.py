"""Workers: the registry and the stop on repeated steps."""

import json
import threading
from types import SimpleNamespace

from helpers import CHILD, main_tool, spawn_worker, start_turn, w_note, w_post, w_tool


def test_worker_only_governed_under_governed_parent(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, session="cli-s", platform="cli")
    plugin._subagent_start(parent_session_id="cli-s", child_session_id="c9", child_subagent_id="sa-9", child_goal="g")
    assert plugin.workers.lookup(task_id="sa-9") is None


def test_worker_reminded_then_stopped_on_repeated_step(plugin, make_ctx):
    posted = []
    make_ctx(post_message=lambda text: posted.append(text) or SimpleNamespace(ok=True))
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

    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status="completed",
                          child_summary=json.dumps({"status": "blocked", "summary": "522 persists"}),
                          tool_call_history=[], duration_ms=60000)
    note = start_turn(plugin, turn="t2")["context"]
    assert "The plugin stopped it" in note and "Tell the user in one line" in note, "the secretary handles it"
    assert posted == [], "nothing goes to the user's chat directly"


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


def test_running_workers_are_not_injected_each_turn(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert start_turn(plugin, turn="t2") is None
    assert plugin.ledger.running("s1")
    assert main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"}, turn="t2") is None
    assert not plugin.ledger.running("s1"), "stopped workers are no longer listed as running"


def test_no_periodic_chat_messages(plugin, make_ctx):
    posted = []
    make_ctx(post_message=lambda text: posted.append(text) or SimpleNamespace(ok=True))
    start_turn(plugin, user="帮我部署一下")
    before = threading.active_count()
    spawn_worker(plugin)
    w_post(plugin, "terminal", {"command": "make deploy"}, json.dumps({"output": "ok", "exit_code": 0}))
    assert threading.active_count() == before, "spawning a worker starts no timer"
    assert posted == []


# ---------------------------------------------------------------- refused steps (issue #7)

DENIED = json.dumps({"output": "", "exit_code": -1, "status": "blocked",
                     "error": "BLOCKED: User denied this command. The user has NOT consented to this action.",
                     "user_summary": "You denied this command — it did not run."})
PENDING = json.dumps({"output": "", "exit_code": -1, "error": "", "status": "pending_approval",
                      "approval_pending": True, "command": "systemctl restart nginx", "description": "restart"})


def _transform(plugin, args, result, name="terminal"):
    out = plugin._transform_tool_result(name, args, result, **CHILD)
    return json.loads(out)["super_agent_note"] if out else None


def test_an_approval_refusal_tells_the_worker_to_bring_it_back(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    rm = {"command": "rm -rf /srv/app/cache"}
    note = _transform(plugin, rm, DENIED)
    assert "approval or a safety rule refused this call" in note and "needs_decision" in note
    assert "Do not retry it, rephrase it or reach the same result another way" in note
    assert _transform(plugin, rm, DENIED) is None, "said once per call"
    assert "refused this call" in _transform(plugin, {"command": "rm -rf /srv/app/tmp"}, DENIED)
    pending = _transform(plugin, {"command": "systemctl restart nginx"}, PENDING)
    assert "waiting for the user's approval" in pending and "cannot wait" in pending
    assert _transform(plugin, {"command": "false"}, json.dumps({"output": "", "exit_code": 1,
                                                                 "error": "command failed"})) is None


def test_a_hook_refusal_is_noted_on_the_next_result(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    push = {"command": "git push origin main"}
    # agent/tool_executor.py::_blocked_tool_result: no transform_tool_result for a blocked call
    plugin._post_tool_call("terminal", push, json.dumps({"error": "pushes to main need review"}), status="blocked",
                           error_type="plugin_block", error_message="pushes to main need review", **CHILD)
    note = _transform(plugin, {"command": "git status"}, json.dumps({"output": "clean", "exit_code": 0}))
    assert "Your earlier call terminal git push origin main was refused by a hook or plugin rule" in note
    assert "pushes to main need review" in note and "needs_decision" in note
    assert _transform(plugin, {"command": "git status"}, json.dumps({"output": "clean", "exit_code": 0})) is None


def test_the_plugins_own_stop_is_not_a_refusal(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    worker["stop_reason"] = "ran the same step 5 times"
    blocked = w_tool(plugin, "terminal", {"command": "ls"})
    plugin._post_tool_call("terminal", {"command": "ls"}, json.dumps({"error": blocked["message"]}),
                           status="blocked", error_type="plugin_block", error_message=blocked["message"], **CHILD)
    assert worker["pending_note"] is None
