"""hermes super-agent status."""

import argparse
import json

from helpers import end_turn, spawn_worker, start_turn


def _run(ctx, *argv):
    entry = ctx.cli_commands["super-agent"]
    parser = argparse.ArgumentParser(prog="hermes super-agent")
    entry["setup_fn"](parser)
    parser.set_defaults(func=entry["handler_fn"])
    args = parser.parse_args(list(argv))
    return args.func(args)


def test_status_lists_settings_and_tasks(plugin, make_ctx, capsys, monkeypatch):
    monkeypatch.setenv("SUPER_AGENT_TURN_SOFT_SECONDS", "45")
    ctx = make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    spawn_worker(plugin, goal="Rotate the Cloudflare token", sub_id="sa-1-def", child_session="child-2")
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-2", child_status="completed",
                          child_summary=json.dumps({"status": "needs_decision", "question": "Which zone?"}),
                          tool_call_history=[], duration_ms=1000)
    end_turn(plugin)

    assert _run(ctx, "status") == 0
    out = capsys.readouterr().out
    assert out.startswith(f"super-agent {plugin.__version__}")
    assert "ledger.db" in out and "turn_soft_seconds" in out and "45.0   (changed)" in out
    assert "worker_repeat_limit" in out and "Host check" in out
    lines = out.splitlines()
    first = next(i for i, line in enumerate(lines) if "running" in line and "Fix the 522" in line)
    second = next(i for i, line in enumerate(lines) if "needs_decision" in line)
    assert first < second, "running tasks come first"
    assert "asks: Which zone?" in out and "chat s1" in out


def test_status_json_and_default_command(plugin, make_ctx, capsys):
    ctx = make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert _run(ctx, "status", "--json", "--limit", "1") == 0
    info = json.loads(capsys.readouterr().out)
    assert info["version"] == plugin.__version__ and info["ledger"].endswith("ledger.db")
    assert [t["id"] for t in info["tasks"]] == ["sa-0-abc"]
    assert info["settings"]["governed_platforms"] == {"value": ["weixin"], "default": ["weixin"]}

    assert _run(ctx) == 0, "no subcommand means status"
    assert "Recent subagent tasks" in capsys.readouterr().out


def test_status_with_an_empty_ledger(plugin, make_ctx, capsys):
    ctx = make_ctx()
    assert _run(ctx, "status") == 0
    assert "No subagent tasks recorded yet." in capsys.readouterr().out


def test_register_without_cli_support(plugin):
    class Bare:
        def get_config(self, key, default=None):
            return default

        def register_hook(self, name, fn, **kwargs):
            pass

        def register_middleware(self, kind, fn):
            pass
    plugin.register(Bare())
