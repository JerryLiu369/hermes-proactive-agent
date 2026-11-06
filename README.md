# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

`proactive-agent` is a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that runs your chat agent like a
personal secretary with a team of workers.

You are the boss. The **main agent** is the secretary: it is always reachable, understands what you want, makes the
decisions and reports back. **Subagents** are the workers: they take the slow, long or noisy jobs.

- [Install](#install)
- [How it works](#how-it-works)
- [Features](#features)
- [Configuration](#configuration)
- [Development](#development)

## Install

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

The plugin lives in the `proactive-agent/` directory of this repository; Hermes installs only that directory.
Manual install: copy `proactive-agent/` to `~/.hermes/plugins/proactive-agent` and run
`hermes plugins enable proactive-agent`.

Recommended Hermes setting: lower the worker step budget. A worker that needs more than about 60 model calls is
usually stuck, and a lower cap makes it report sooner.

```yaml
delegation:
  max_iterations: 60
```

## How it works

The plugin follows one task through its life and does one simple thing at each step.

```
 You ──► Secretary (main agent) ──── delegate_task ────► Worker (subagent)
          1. Turn budget            2. Handoff            3. Worker limits
             ▲                                                 │
             └────────── 4. Results and task ledger ◄──────────┘
```

1. **The secretary stays responsive.** It may investigate and think, but a turn has a time budget, and long commands
   run in the background.
2. **The secretary writes the brief.** It already knows the conversation, so it writes the handoff itself. The plugin
   only returns a brief that is clearly too short, and attaches what the worker cannot know: your recent messages, your
   memory and a few working rules.
3. **Workers do not spin.** A worker is stopped when it keeps repeating the same step. Its step budget is Hermes' own
   `delegation.max_iterations`.
4. **The loop closes.** The secretary hears how each worker ended and what to check. A task ledger remembers every
   task across restarts, so the next worker starts from what the last one found.

Safety checks that Hermes already does are left to Hermes: subagents' dangerous commands are denied by default
(`delegation.subagent_auto_approve: false`), and file tools refuse stale overwrites. The plugin makes no extra model
calls.

## Features

### 1. Turn budget

| Time in one turn | Effect |
|---|---|
| under 60 s | nothing |
| from 60 s | a one-time note on the next tool result: answer the user or delegate within 1-2 steps |
| from 120 s | editing and execution tools are paused; reading, `delegate_task` and wrap-up tools (`memory`, `todo`, `clarify`, `send_message`, and `process_manage` to list, poll, read logs or kill) stay available |

- A message from you mid-turn resets the budget.
- Foreground commands longer than 30 s move to the background through Hermes' own yield path. The process keeps
  running and Hermes notifies the secretary when it finishes; the secretary is told the command has not failed.
- `process_manage(action='wait')` is refused: waiting on a background job would block the chat again.
- A short system prompt section explains these rules to the secretary up front.

### 2. Handoff

- A `delegate_task` whose `context` is under 120 characters is returned once per turn with a short checklist: what is
  known, exact targets, what must not change, when to stop, what proves success. The second attempt always goes
  through.
- A `delegate_task` that names a file another running worker is changing is also returned once per turn: wait for
  that worker, steer it, or stop it. The second attempt always goes through.
- Every accepted task gets, after the secretary's own context:
  - your last few messages, verbatim, so constraints like "don't touch X" reach the worker;
  - your `USER.md` and `MEMORY.md`, in full (Hermes does not load memory into subagents and already caps both files);
  - from the task ledger: the other workers running now and the files they are changing, and this chat's recent
    unfinished tasks (summary, blockers, corrections, changed files, full log path);
  - working rules: stay in scope, stop after the same failure twice, risky changes only with a rollback path, leave
    files another worker is changing alone, check the real end state;
  - a default `output_schema` (`status`, `summary`, `evidence`, `blockers`) if the task had none.

### 3. Worker limits

These apply to subagents spawned by a governed session.

- **Same step, same result.** The same tool with the same arguments returning the same result: after 3 repeats the
  worker gets a note to change approach; after 5 it is stopped. Its next tool calls are refused and it must report
  status `blocked`. You get a short alert in the chat.
- **Step budget.** Left to Hermes' `delegation.max_iterations`. When it runs out, Hermes gives the worker one last call
  without tools to write its report and tells the secretary the task was cut short.

### 4. Results and task ledger

- When a worker returns, the secretary's next turn gets a note: the evidence the worker reported, or a warning that it
  claimed success without evidence, did not finish, or was stopped by the plugin.
- The secretary checks running workers on demand with `delegate_task(action='list')`, and the system prompt asks it to
  stop superseded ones (`action='stop'`). Nothing is injected into every turn, and nothing is posted on a timer.
- The task ledger keeps one record per task: goal, status, files it changed, corrections sent with
  `action='steer'`, its live transcript path, and how it ended (summary, evidence, blockers). The plugin's hooks write
  it, with no model calls. It lives in `<hermes home>/plugin-data/proactive-agent/ledger.db` and keeps each chat's
  last 20 finished tasks for up to 7 days.
- A task the secretary stops with `action='stop'` is recorded as stopped at once, so it leaves the running list but
  its partial work still reaches the next worker; its result note says it was stopped on purpose.
- A running record whose end never arrives stops counting as running after 6 hours, like the in-memory worker list.
- Hermes does not resume a subagent after it ends or after a restart. Instead, the next worker in the same chat is
  briefed with the unfinished task's record. After a restart, the secretary is told which tasks were running, that
  their side effects are unknown, and which files they had changed.
- Result and restart notes wait in the ledger until that chat's next turn, so a restart, or a CLI process loading the
  plugin first, does not lose them.
- The only chat message the plugin sends itself is the alert when a worker is stopped for repeating itself (gateway
  platforms, via `ctx.post_message`).

### Scope

Only platforms in `governed_platforms` are affected (default `["weixin"]`). Other platforms, cron runs, and subagents
spawned by ungoverned sessions are untouched. A plugin error is logged and never blocks a tool.

## Configuration

Settings live under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`. Keys placed directly under
`proactive-agent:` are ignored by Hermes. Each key can also be overridden with an environment variable
`PROACTIVE_<KEY>` (upper case).

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
```

| Key | Default | Meaning |
|---|---|---|
| `governed_platforms` | `["weixin"]` | Platforms whose main agent (and its subagents) are governed |
| `terminal_auto_detach_seconds` | `30` | Foreground commands longer than this move to the background (0 disables) |
| `turn_soft_seconds` | `60` | When the secretary is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | When editing and execution tools pause for the rest of the turn |
| `handoff_min_context_chars` | `120` | Shorter `context` is returned once per turn |
| `worker_repeat_limit` | `5` | Same step with the same result this many times stops a worker (note two repeats earlier) |

## Development

```bash
uv run --group dev pytest          # or: pip install --group dev && pytest
uv run --group dev ruff check
```

`tests/test_hermes_e2e.py` loads the plugin through a real Hermes checkout when `hermes_cli` is importable, and is
skipped otherwise.

```
.
├── proactive-agent/       the plugin (what Hermes installs)
│   ├── plugin.yaml        manifest: hooks and settings schema
│   ├── __init__.py        entry point: register() wires the hooks
│   ├── config.py          settings lookup
│   ├── context.py         who is calling: governed main agent, worker or cron
│   ├── secretary.py       turn budget, background detach
│   ├── handoff.py         delegate_task check and briefing
│   ├── workers.py         repeat stop, result notes, stuck alert
│   ├── ledger.py          persistent task ledger (SQLite in plugin-data)
│   ├── prompt.py          system prompt section
│   └── util.py            text and tool-result helpers
├── tests/                 one test file per module, plus end-to-end tests against Hermes
├── pyproject.toml         project metadata, dev dependencies, ruff and pytest settings
└── llms.txt               summary for language models
```

## Compatibility

- Hermes Agent 0.22 or later, with `pre_tool_call` arg rewriting, `subagent_start` / `subagent_stop` and
  `ctx.post_message` (tested on v0.22.7). Without `post_message` there is no stuck alert in the chat.
- Python 3.10 or later.

## License

[MIT](LICENSE)
