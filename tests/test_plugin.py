"""Behaviour tests for proactive-agent: turn budget, handoff check, worker limits, results and progress."""

import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from conftest import REPO_ROOT

GOOD_CONTEXT = (
    "Known: https://app.example.com/ping returns 522 since the deploy at 10:00; DNS record is proxied. "
    "Target: /srv/app/wrangler.toml and the worker route for app.example.com. Do not touch other routes. "
    "Stop after one redeploy if 522 persists. Evidence: curl -sI status line of the public URL."
)
CHILD = dict(task_id="sa-0-abc", session_id="child-1", turn_id="ct")


def start_turn(plugin, session="s1", turn="t1", platform="weixin", history=None, user="please fix the site"):
    return plugin._pre_llm_call(session_id=session, turn_id=turn, platform=platform,
                                conversation_history=history or [], user_message=user)


def main_tool(plugin, name, args=None, session="s1", turn="t1"):
    return plugin._pre_tool_call(name, args or {}, session_id=session, turn_id=turn, task_id="main")


def spawn_worker(plugin, goal="Fix the 522 on app.example.com", context=GOOD_CONTEXT, sub_id="sa-0-abc",
                 child_session="child-1"):
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": goal, "context": context}]})
    assert res and res["action"] == "modify", res
    plugin._subagent_start(parent_session_id="s1", child_session_id=child_session, child_subagent_id=sub_id,
                           child_goal=goal)
    worker = plugin.workers.lookup(task_id=sub_id)
    assert worker is not None
    return worker


def w_tool(plugin, name, args, api="r1"):
    return plugin._pre_tool_call(name, args, api_request_id=api, **CHILD)


def w_post(plugin, name, args, result, api="r1"):
    plugin._post_tool_call(name, args, result, api_request_id=api, **CHILD)


def w_note(plugin, result='{"output": "x"}'):
    out = plugin._transform_tool_result("terminal", {}, result, **CHILD)
    return json.loads(out)["proactive_agent_note"] if out else None


# ---------------------------------------------------------------- manifest & registration

def test_manifest_matches_code(plugin):
    with open(os.path.join(REPO_ROOT, "plugin.yaml"), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    assert manifest["version"] == plugin.__version__ == "0.1.11"
    registered = {}
    plugin.register(SimpleNamespace(register_hook=lambda n, fn: registered.setdefault(n, fn),
                                    register_system_prompt_section=lambda *a, **k: None, get_config=lambda k, d=None: d))
    assert set(manifest["hooks"]) == set(registered)
    schema = manifest["config_schema"]
    assert set(schema) == set(plugin.config.DEFAULTS)
    for key, default in plugin.config.DEFAULTS.items():
        assert schema[key]["default"] == default, key


def test_prompt_only_for_governed_main(plugin, make_ctx):
    ctx = make_ctx()
    render = ctx.prompt_sections["proactive-agent.discipline"]["renderer"]
    text = render({"platform": "weixin"})
    assert "~60s" in text and "At 120s" in text and "30s" in text and "action='list'" in text
    for info in ({"platform": "cli"}, {"platform": "subagent"}, {"platform": "cron"},
                 {"platform": "weixin", "session_id": "cron_1"}, None, {}):
        assert render(info) == ""


def test_config_from_env(plugin, make_ctx):
    make_ctx(config={"worker_repeat_limit": 9})
    assert plugin.config.get("worker_repeat_limit") == 9
    assert plugin.config.get("progress_interval_seconds") == 600
    with patch.dict(os.environ, {"PROACTIVE_WORKER_REPEAT_LIMIT": "7", "PROACTIVE_GOVERNED_PLATFORMS": "a, b"}):
        assert plugin.config.get("worker_repeat_limit") == 7
        assert plugin.config.governed_platforms() == ["a", "b"]


# ---------------------------------------------------------------- secretary turn budget

def test_turn_budget_reminds_then_pauses_edits(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "terminal", {"command": "ls"}) is None

    plugin.secretary._turns["s1"]["start"] -= 65
    assert main_tool(plugin, "terminal", {"command": "ls"}) is None, "the reminder zone never blocks"
    note = plugin._transform_tool_result("terminal", {}, '{"output": "ok"}', session_id="s1", task_id="main")
    assert "Within the next 1-2 steps" in json.loads(note)["proactive_agent_note"]
    assert plugin._transform_tool_result("terminal", {}, '{"output": "ok"}', session_id="s1", task_id="main") is None

    plugin.secretary._turns["s1"]["start"] -= 60
    blocked = main_tool(plugin, "patch", {"path": "/x"})
    assert blocked["action"] == "block" and "more than 120s" in blocked["message"]
    for allowed in ("read_file", "memory", "process_manage"):
        assert main_tool(plugin, allowed, {}) is None, allowed
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "g", "context": GOOD_CONTEXT}]})
    assert res["action"] == "modify"


def test_large_tool_output_does_not_pause_edits(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    plugin._post_tool_call("read_file", {"path": "/x"}, "a" * 500000, session_id="s1", turn_id="t1", task_id="main")
    assert main_tool(plugin, "patch", {"path": "/x"}) is None


def test_new_turn_and_steer_reset_budget(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    plugin.secretary._turns["s1"]["start"] -= 200
    assert main_tool(plugin, "terminal", {"command": "ls"})["action"] == "block"
    steer = [{"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] stop, do X instead"}]
    plugin._pre_api_request(session_id="s1", turn_id="t1", request_messages=steer)
    assert main_tool(plugin, "terminal", {"command": "ls"}) is None
    plugin.secretary._turns["s1"]["start"] -= 200
    start_turn(plugin, turn="t2")
    assert main_tool(plugin, "terminal", {"command": "ls"}, turn="t2") is None


def test_ungoverned_platforms_and_cron_are_untouched(plugin, make_ctx):
    make_ctx()
    for session, platform in (("s-cli", "cli"), ("s-tg", "telegram"), ("cron_abc_1", "weixin")):
        start_turn(plugin, session=session, platform=platform)
        assert main_tool(plugin, "process_manage", {"action": "wait"}, session=session) is None
        assert main_tool(plugin, "delegate_task", {"tasks": [{"goal": "g"}]}, session=session) is None


def test_main_agent_cannot_wait_on_background_jobs(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "process_manage", {"action": "wait"})["action"] == "block"
    assert main_tool(plugin, "process_manage", {"action": "list"}) is None
    assert main_tool(plugin, "terminal", {"command": "grep -rn x ~/.hermes"}) is None


def test_yielded_terminal_result_explains_background(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    raw = json.dumps({"output": "Uploading 40%", "status": "yielded_to_background", "session_id": "proc_1"})
    out = json.loads(plugin._transform_tool_result("terminal", {}, raw, session_id="s1", task_id="main"))
    assert "NOT failed" in out["note"] and out["output"] == "Uploading 40%"


def test_plugin_errors_never_block_tools(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    with patch.object(plugin.secretary, "pre_tool_call", side_effect=RuntimeError("boom")):
        assert main_tool(plugin, "terminal", {"command": "ls"}) is None


# ---------------------------------------------------------------- handoff

def test_thin_handoff_returned_once_then_briefed(plugin, make_ctx):
    make_ctx()
    history = [{"role": "user", "content": "autosub 又 522 了，别自己改 edgetunnel 的代码"},
               {"role": "assistant", "content": "我看了，DNS 是代理状态"}]
    start_turn(plugin, history=history, user="帮我修一下")
    first = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "修复 522", "context": None}]})
    assert first["action"] == "block" and "exact targets" in first["message"]

    second = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "修复 522", "context": None}]})
    assert second["action"] == "modify"
    task = second["args"]["tasks"][0]
    assert plugin.handoff.BRIEFING_MARKER in task["context"]
    assert "别自己改 edgetunnel" in task["context"] and "帮我修一下" in task["context"]
    assert "DNS 是代理状态" not in task["context"], "only the user's own words are attached"
    assert "final report in" not in task["context"]
    assert task["output_schema"]["required"] == ["status", "summary"]


def test_steer_joins_user_words_but_injected_context_does_not(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, user="deploy the site")
    request = [{"role": "user", "content": "deploy the site\n\n[proactive-agent] Subagents still running: ..."},
               {"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] use the staging config"}]
    plugin._pre_api_request(session_id="s1", turn_id="t1", request_messages=request)
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Deploy", "context": GOOD_CONTEXT}]})
    briefing = res["args"]["tasks"][0]["context"]
    assert "use the staging config" in briefing and "Subagents still running" not in briefing


def test_handoff_keeps_brief_and_attaches_memory(plugin, make_ctx, tmp_path):
    (tmp_path / "memories").mkdir()
    long_memory = "Never deploy with --env production. " + "x" * 9000 + " END-OF-MEMORY"
    (tmp_path / "memories" / "MEMORY.md").write_text(long_memory, encoding="utf-8")
    (tmp_path / "memories" / "USER.md").write_text("Prefers short answers", encoding="utf-8")
    make_ctx()
    with patch.dict(sys.modules, {"hermes_constants": SimpleNamespace(get_hermes_home=lambda: tmp_path)}):
        start_turn(plugin)
        res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix 522", "context": GOOD_CONTEXT,
                                                             "output_schema": {"type": "object"}}]})
    task = res["args"]["tasks"][0]
    assert task["context"].startswith(GOOD_CONTEXT)
    assert "Never deploy with --env production" in task["context"] and "END-OF-MEMORY" in task["context"]
    assert "Prefers short answers" in task["context"]
    assert task["output_schema"] == {"type": "object"}


def test_single_goal_shape(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    res = main_tool(plugin, "delegate_task", {"goal": "Fix", "context": GOOD_CONTEXT})
    assert res["action"] == "modify" and "tasks" not in res["args"]
    assert plugin.handoff.BRIEFING_MARKER in res["args"]["context"]


def test_other_delegate_actions_pass_through(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "delegate_task", {"action": "list"}) is None
    assert main_tool(plugin, "delegate_task", {"action": "steer", "subagent_id": "sa-1", "message": "x"}) is None


# ---------------------------------------------------------------- workers

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


# ---------------------------------------------------------------- results and progress

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
    plugin._on_session_clean(session_id="s1")
    assert start_turn(plugin, turn="t2") is None


def test_running_workers_are_not_injected_each_turn(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert start_turn(plugin, turn="t2") is None
    assert plugin.workers.active_for_parent("s1")
    assert main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"}, turn="t2") is None
    assert not plugin.workers.active_for_parent("s1"), "stopped workers drop out of progress lines"


def test_progress_tick_posts_to_chat(plugin, make_ctx):
    posted = []
    done = threading.Event()

    def post(text):
        posted.append(text)
        done.set()
        return SimpleNamespace(ok=True)

    make_ctx(post_message=post, config={"progress_interval_seconds": 3600})
    start_turn(plugin, user="帮我部署一下")
    spawn_worker(plugin)
    plugin.workers._progress_tick("s1")
    assert done.wait(2)
    assert posted[0].startswith("后台进展：1 个子任务在跑")


# ---------------------------------------------------------------- background detach

class _FakeEnv:
    def _wait_for_process(self, proc, timeout=120, **kwargs):
        return {"returncode": 0, "output": "done"}


def _install_patch(plugin):
    base = MagicMock()
    base.BaseEnvironment = _FakeEnv
    modules = {"tools": MagicMock(), "tools.environments": MagicMock(), "tools.environments.base": base,
               "tools.interrupt": MagicMock()}
    original = _FakeEnv._wait_for_process
    plugin.secretary._patched = False
    with patch.dict(sys.modules, modules):
        plugin.secretary.install_detach_patch()
    return original


def _wait(**kw):
    return _FakeEnv()._wait_for_process(MagicMock(), timeout=120, watch_interrupt_tid=1, yield_handler=MagicMock())


def test_detach_timer_only_for_governed_main(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        start_turn(plugin, session="cli-1", platform="cli")
        for session, expected in (("wx-1", True), ("cli-1", False)):
            with patch.dict(os.environ, {"HERMES_SESSION_ID": session}), patch("threading.Timer") as timer:
                assert _wait()["output"] == "done"
                assert timer.called is expected, session
                if expected:
                    assert timer.call_args[0][0] == 30.0
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False


def test_detach_leaves_a_foreign_yield_alone(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    interrupt = MagicMock()
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "wx-1"}), \
                patch.dict(sys.modules, {"tools": MagicMock(), "tools.interrupt": interrupt}), \
                patch("threading.Timer"):
            _wait()
        interrupt.consume_yield.assert_not_called()
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False


def test_detach_skipped_for_children(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    child_ctx = SimpleNamespace(is_delegated_child_context=lambda: True)
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "wx-1"}), \
                patch.dict(sys.modules, {"agent": MagicMock(), "agent.delegation_context": child_ctx}), \
                patch("threading.Timer") as timer:
            _wait()
            timer.assert_not_called()
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False
