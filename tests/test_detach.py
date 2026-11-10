"""Background detach of long foreground commands, through the tool_execution middleware."""

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from helpers import start_turn


class _Timer:
    """Stands in for threading.Timer: records the delay and fires on demand."""
    made = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.cancelled, self.daemon = delay, fn, False, False
        _Timer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def interrupt():
    _Timer.made = []
    mod = MagicMock()
    with patch.dict(sys.modules, {"tools": MagicMock(), "tools.interrupt": mod}), patch("threading.Timer", _Timer):
        yield mod


def _run(ctx, session="s1", tool="terminal", args=None, during=None, task_id="main"):
    """Call the middleware the way Hermes does; ``during`` runs while the command is "running"."""
    def next_call(payload):
        if during:
            during()
        return '{"output": "done"}'
    return ctx.middleware["tool_execution"](tool_name=tool, args=args or {"command": "make test"},
                                            next_call=next_call, session_id=session, task_id=task_id,
                                            turn_id="t1", tool_call_id="c1")


def test_detach_timer_only_for_governed_main_terminal(plugin, make_ctx, interrupt):
    ctx = make_ctx()
    start_turn(plugin, session="wx-1", platform="weixin")
    start_turn(plugin, session="cli-1", platform="cli")
    assert _run(ctx, session="wx-1") == '{"output": "done"}'
    _run(ctx, session="cli-1")
    _run(ctx, session="wx-1", tool="read_file", args={"path": "x"})
    _run(ctx, session="wx-1", args={"command": "serve", "background": True})
    assert [t.delay for t in _Timer.made] == [30.0]
    assert _Timer.made[0].cancelled


def test_detach_fires_a_yield_on_the_tool_thread_and_consumes_it(plugin, make_ctx, interrupt):
    ctx = make_ctx()
    start_turn(plugin)
    _run(ctx, during=lambda: _Timer.made[0].fn())  # the command outlives the detach delay
    interrupt.request_yield.assert_called_once_with(threading.get_ident())
    interrupt.consume_yield.assert_called_once_with(threading.get_ident())


def test_detach_leaves_a_foreign_yield_alone(plugin, make_ctx, interrupt):
    ctx = make_ctx()
    start_turn(plugin)
    _run(ctx)  # finished before the delay
    _Timer.made[0].fn()  # a late timer tick after the call ended does nothing
    interrupt.request_yield.assert_not_called()
    interrupt.consume_yield.assert_not_called()


def test_detach_disarmed_when_the_command_raises(plugin, make_ctx, interrupt):
    ctx = make_ctx()
    start_turn(plugin)

    def boom(payload):
        _Timer.made[0].fn()
        raise RuntimeError("tool failed")
    with pytest.raises(RuntimeError):
        ctx.middleware["tool_execution"](tool_name="terminal", args={"command": "x"}, next_call=boom,
                                         session_id="s1", task_id="main", turn_id="t1")
    assert _Timer.made[0].cancelled
    interrupt.consume_yield.assert_called_once()


def test_detach_skipped_for_children(plugin, make_ctx, interrupt):
    ctx = make_ctx()
    start_turn(plugin)
    _run(ctx, task_id="sa-0-abc", session="child-1")
    _run(ctx, task_id="subagent-0-abc", session="child-2")
    assert _Timer.made == []


def test_detach_disabled_by_setting(plugin, make_ctx, interrupt):
    ctx = make_ctx(config={"terminal_auto_detach_seconds": 0})
    start_turn(plugin)
    _run(ctx)
    assert _Timer.made == []
