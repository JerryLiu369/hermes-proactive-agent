"""Shared constants and call helpers for the behaviour tests."""

import json
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

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
