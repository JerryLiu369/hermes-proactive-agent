"""Main agent: turn budget, background-job waits and governed scope."""

import json
from unittest.mock import patch

from helpers import GOOD_CONTEXT, main_tool, start_turn


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
    for allowed, args in (("read_file", {}), ("memory", {}), ("process_manage", {"action": "list"})):
        assert main_tool(plugin, allowed, args) is None, allowed
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


def test_red_zone_keeps_only_reading_and_wrap_up(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    plugin.secretary._turns["s1"]["start"] -= 130
    for action in ("list", "poll", "log", "kill"):
        assert main_tool(plugin, "process_manage", {"action": action}) is None, action
    for action in ("write", "submit"):
        assert main_tool(plugin, "process_manage", {"action": action})["action"] == "block", action
    assert main_tool(plugin, "skill_manage", {"action": "create"})["action"] == "block"


def test_quoted_yield_json_does_not_replace_the_result(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    raw = 'previous run printed {"status": "yielded_to_background", "session_id": "p1"} and then more output'
    assert plugin._transform_tool_result("terminal", {}, raw, session_id="s1", task_id="main") is None
