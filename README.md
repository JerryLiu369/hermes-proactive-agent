# Hermes Proactive Agent (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.1.9-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

`proactive-agent` is a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that runs your chat agent like a
personal secretary with a team of workers.

You are the boss. The **main agent** is the secretary: it is always reachable, understands what you want, makes the
decisions and reports back. **Subagents** are the workers: they take the slow, long or noisy jobs, with a complete
brief, under supervision.

## Design

The plugin follows one task through its whole life and adds a rule at each step.

```
 You ──► Secretary (main agent) ──── delegate_task ────► Worker (subagent)
          1. Turn budget            2. Handoff contract   3. Worker guards
          long commands to the      brief checked and     fail fast, step budget,
          background                context attached      risky-command and write guards
             ▲                                                 │
             └──────────── 4. Result check and progress ◄──────┘
```

Three principles drive every rule:

- **The secretary thinks, workers execute.** The secretary keeps enough room to investigate and decide, but never
  holds the chat for minutes. Slow work goes to the background or to a worker.
- **A handoff carries everything the worker needs.** A worker starts with no memory and cannot see the chat, so each
  brief must say what is known, what the target is, what must not change and what proves success. The plugin checks
  the brief and attaches the rest.
- **Workers are supervised, results are verified.** Workers stop early when they are stuck, stay inside what the brief
  authorized, and do not overwrite others' work. The secretary checks the evidence before telling you a job is done.

## What it does

### 1. Turn budget: keep the secretary responsive

Each turn of the main agent has a time and tool-output budget.

| Zone | Default trigger (configurable) | Effect |
|---|---|---|
| Green | below both limits | nothing |
| Yellow | 60 s into the turn, or 30,000 chars of tool output | a one-time note on the next tool result: answer the user or delegate within 1-2 steps |
| Red | 120 s, or 80,000 chars of tool output | editing and execution tools are refused; read-only tools and `delegate_task` stay available |

- A message from you mid-turn resets the budget.
- Foreground commands longer than 30 s move to the background through Hermes' own yield path. The process keeps
  running and Hermes notifies the secretary when it finishes. The secretary is told the command has not failed and sees
  the output so far.
- Things that only block the chat are refused: `process_manage(action='wait')`, unbounded `grep -r` / `rg` / `find`
  over home, `/` or `~/.hermes`, and port scans.
- A short system prompt section explains these rules to the secretary up front.

### 2. Handoff contract: give the worker the full picture

Every `delegate_task` call is checked before it runs.

- **Thin brief, returned once.** If `context` is under 120 characters, or the task touches risky ground (SSH, firewall,
  deletes, VM lifecycle) without a rollback path, the call is returned once per turn with a checklist of what to add.
  The second attempt always goes through, so the secretary is never stuck.
- **Context attached automatically.** Every accepted task gets:
  - a summary of the conversation (your intent, facts established, what was tried, what must not change), written by
    an auxiliary model with a plain extract of recent messages and tool results as fallback;
  - your `USER.md` and `MEMORY.md` (capped);
  - working rules: stay in scope, fail fast, no unbounded scans, verify the real end state, report blockers, reply in
    your language;
  - a default `output_schema` (`status`, `summary`, `evidence`, `blockers`, `files_changed`) if the task had none.
- The summary runs on the `proactive_handoff_summary` auxiliary task, which follows your `compression` model unless you
  pin one.

### 3. Worker guards: supervise the work

These apply to subagents spawned by a governed session.

- **Fail fast.** The same call with the same result 3 times, or the same failure 3 times, stops the worker: its next
  tool calls are refused and it is told to report status `blocked` with the exact blocker.
- **Step budget.** 40 tool rounds by default, with a wrap-up warning at 80%.
- **Risky commands** (SSH/firewall changes, HTTP DELETE against APIs, `rm -rf` on system or home paths, cloud
  terminate/delete, reboot, ...) are refused unless the brief included a rollback path.
- **No blind overwrites.** A worker cannot `write_file` / `patch` a file that someone else changed after the worker
  started, until it re-reads it.

### 4. Result check and progress: close the loop

- When a worker returns, the secretary's next turn gets a note: the evidence the worker reported, a warning that it
  claimed success without evidence, or that the plugin stopped it.
- Each turn the secretary sees which workers are still running.
- If a new task targets the same paths, hosts or domains as a running worker, the secretary is asked once to stop the
  old worker (`delegate_task(action='stop')`) first, so a late result cannot overwrite the new work.
- While workers run, a progress line is posted to the chat every 5 minutes, and a short alert when a worker gets stuck
  (gateway platforms, via `ctx.post_message`, in your language).

### Scope

Only platforms in `governed_platforms` are affected (default `["weixin"]`). Other platforms, cron runs, and subagents
spawned by ungoverned sessions are untouched.

## Configuration

All keys live under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`. Keys placed directly under
`proactive-agent:` are ignored by Hermes. Each key can also be overridden with an environment variable
`PROACTIVE_<KEY>` (upper case).

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

Keep `delegation.max_iterations` above `worker_max_tool_rounds`, so the plugin's budget (which asks the worker for a
report) fires before Hermes' hard cap.

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
`tests/test_hermes_e2e.py` runs against a real Hermes checkout when `hermes_cli` is importable and is skipped otherwise.

| Module | Role |
|---|---|
| `__init__.py` | hook wiring |
| `config.py` | settings |
| `context.py` | who is calling: governed main agent, worker, or cron |
| `secretary.py` | turn budget, background detach, conversation snapshot |
| `handoff.py` | `delegate_task` check and attached context |
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
