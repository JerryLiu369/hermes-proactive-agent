# Hermes Proactive Agent (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.2.0-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

`proactive-agent` is a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that turns the chat agent into a
personal assistant that manages workers, instead of a worker that blocks the chat.

- The **main agent** (the "secretary") stays responsive, does the thinking, and owns the conversation.
- **Subagents** (the "workers") do slow execution, with a complete brief and under supervision.

## Why 0.2

Field data from 0.1.x ([issue #1](https://github.com/JerryLiu369/hermes-proactive-agent/issues/1)) showed the
governance was pointed the wrong way: the main agent was cut off after 5 tool rounds / 90 s and forced to delegate
before it understood the problem, while subagents ran unsupervised for up to 250 rounds. Subagents start with no
memory and cannot see the chat, so they got near-empty briefs, looped on the same failing command, scanned ports,
overwrote each other's work and reported unverified success.

0.2 reverses this: the secretary gets room to think and a gentle budget, every handoff carries what the secretary
knows, and workers are held to tight, checkable rules.

## What it does

### 1. Secretary turn budget (main agent)

| Zone | Default trigger (configurable) | Effect |
|---|---|---|
| Green | below both limits | nothing |
| Yellow | 60 s into the turn, or 30,000 chars of tool output | a one-time note on the next tool result: answer the user or delegate within 1-2 steps |
| Red | 120 s, or 80,000 chars of tool output | editing and execution tools are refused; read-only tools and `delegate_task` stay available |

- A user message mid-turn (steer) resets the budget.
- Foreground commands longer than 30 s move to the background through Hermes' own yield path (the process keeps
  running and notifies on completion). The result tells the agent the command has not failed and shows the output so far.
- `process_manage(action='wait')`, unbounded `grep -r` / `rg` / `find` over home, `/` or `~/.hermes`, and port scans are refused.

### 2. Handoff contract (`delegate_task`)

- **Thin brief: returned once.** If `context` is under 120 characters, or the task touches risky ground (SSH, firewall,
  deletes, VM lifecycle) without a rollback path, the call is refused once per turn with a checklist of what to add.
  The second attempt always goes through.
- **Briefing attached automatically.** Every accepted task gets:
  - a summary of the conversation (intent, facts established, what was tried, what must not change), generated with
    an auxiliary model call (`ctx.llm`, 12 s timeout) and falling back to a plain extract of recent messages and tool results;
  - the user's `USER.md` and `MEMORY.md` (capped);
  - working rules: stay in scope, fail fast, no unbounded scans, verify the real end state, report blockers;
  - a default `output_schema` (`status`, `summary`, `evidence`, `blockers`, `files_changed`) if the task had none.
- The summary runs on the `proactive_handoff_summary` auxiliary task, which follows your `compression` model unless you pin one.

### 3. Worker guards (subagents of governed sessions)

- **Fail fast.** The same call with the same result 3 times, or the same failure 3 times, stops the worker: its next
  tool calls are refused and it is told to report status `blocked` with the exact blocker.
- **Step budget.** 40 tool rounds by default, with a wrap-up warning at 80%.
- **Risky commands** (SSH/firewall changes, HTTP DELETE against APIs, `rm -rf` on system or home paths, cloud
  terminate/delete, reboot, ...) are refused unless the handoff included a rollback path.
- **No blind overwrites.** A worker cannot `write_file` / `patch` a file that someone else changed after the worker
  started, until it re-reads it.

### 4. Result checks, pivots and progress

- When a worker returns, the secretary's next turn gets a note: the evidence the worker reported, or a warning that it
  claimed success without evidence, or that the plugin stopped it.
- Each turn the secretary sees which workers are still running.
- If a new task targets the same paths, hosts or domains as a running worker, the secretary is asked once to stop the
  old worker (`delegate_task(action='stop')`) first.
- While workers run, a progress line is posted to the chat every 5 minutes, and a short alert when a worker gets stuck
  (gateway platforms; uses `ctx.post_message`, in the user's language).

Only platforms in `governed_platforms` are affected (default `["weixin"]`). Other platforms, cron runs, and subagents
spawned by ungoverned sessions are untouched.

## Configuration

All keys live under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml` (Hermes also reads the older
`config:` subtree; keys placed directly under `proactive-agent:` are ignored by Hermes). Each key can also be overridden
with an environment variable `PROACTIVE_<KEY>` (upper case).

| Key | Default | Meaning |
|---|---|---|
| `governed_platforms` | `["weixin"]` | Platforms whose main agent (and its subagents) are governed |
| `terminal_auto_detach_seconds` | `30` | Foreground commands longer than this move to the background (0 disables) |
| `turn_soft_seconds` | `60` | Yellow zone start |
| `turn_hard_seconds` | `120` | Red zone start |
| `turn_soft_output_chars` | `30000` | Yellow zone also starts after this much tool output |
| `turn_hard_output_chars` | `80000` | Red zone also starts after this much tool output |
| `handoff_min_context_chars` | `120` | Shorter `context` is returned once |
| `handoff_summary_enabled` | `true` | Use an auxiliary model for the chat summary |
| `handoff_summary_timeout_seconds` | `12` | Keep well under `plugins.hook_callback_timeout` (30 s) |
| `handoff_memory_max_chars` | `4000` | Memory attached per handoff (0 disables) |
| `worker_max_tool_rounds` | `40` | Worker step budget |
| `worker_repeat_limit` | `3` | Repeats before a worker is stopped |
| `worker_guard_risky_commands` | `true` | Refuse risky worker commands without a rollback path |
| `worker_write_guard` | `true` | Refuse blind overwrites of files changed by others |
| `verify_child_results` | `true` | Tell the secretary what to verify when a worker returns |
| `progress_push_enabled` | `true` | Post progress lines and stuck alerts to the chat |
| `progress_interval_seconds` | `300` | Seconds between progress lines |

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        worker_max_tool_rounds: 40
```

Recommended delegation settings: keep `delegation.max_iterations` above `worker_max_tool_rounds` so the plugin's
budget (which asks for a report) fires before Hermes' hard cap.

### Upgrading from 0.1.x

`turn_max_tool_rounds` and `turn_max_duration_seconds` are gone; the round-count circuit breaker was the main cause of
premature, context-free delegation. Use `turn_soft_seconds` / `turn_hard_seconds` instead.

## Install

```bash
hermes plugins install github:JerryLiu369/hermes-proactive-agent
hermes plugins enable proactive-agent
```

Manual install: clone into `~/.hermes/plugins/proactive-agent` and run `hermes plugins enable proactive-agent`.

## Development

```bash
pip install pytest pyyaml
pytest -q
```

The repo root is the plugin package; tests load it as a package (see `tests/conftest.py`).

| Module | Role |
|---|---|
| `__init__.py` | hook wiring |
| `config.py` | settings |
| `context.py` | who is calling: governed main agent, child, or cron |
| `secretary.py` | turn budget, background detach, conversation snapshot |
| `handoff.py` | `delegate_task` gate and briefing |
| `workers.py` | worker registry, guards, result checks, progress |
| `guards.py` | command classification and text helpers |
| `prompt.py` | system prompt section |

## Compatibility

- Hermes Agent with `pre_tool_call` arg rewriting, `subagent_start` / `subagent_stop`, `ctx.llm` and `ctx.post_message`
  (tested against Hermes v0.22.7). Missing optional features degrade quietly: no summary model falls back to a plain
  extract; no `post_message` means no progress lines.
- Python 3.10+.

## License

[MIT](LICENSE)
