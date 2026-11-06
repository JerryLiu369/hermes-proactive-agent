# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that runs your chat agent like a secretary with a team
of workers.

You are the boss. The **main agent** is the secretary: always reachable, it understands what you want, decides, and
reports back. **Subagents** are the workers: they take the slow, long or noisy jobs. The plugin keeps that division
honest, so the chat never freezes, workers start with what they need, and nothing they do is forgotten.

## Install

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

Hermes installs only the `proactive-agent/` directory. Restart the gateway afterwards. To upgrade, run
`hermes plugins update proactive-agent`.

Recommended: a worker that needs more than about 60 model calls is usually stuck, so lower Hermes' step budget and it
reports sooner.

```yaml
delegation:
  max_iterations: 60
```

## How it works

The plugin follows a task through its life and does one small thing at each stage.

```
 You ──► Secretary ─────── delegate_task ───────► Worker
         1. Turn budget      2. Handoff           3. Worker limits
            ▲                                          │
            └────────── 4. Results and ledger ◄────────┘
```

1. **Turn budget.** The secretary may think and investigate, but every turn has a time limit, and long commands run in
   the background. You always get an answer quickly.
2. **Handoff.** The secretary knows the conversation, so it writes the brief. The plugin sends back a brief that is too
   thin, and attaches what a worker cannot know on its own.
3. **Worker limits.** A worker that keeps repeating the same step is stopped instead of burning its budget.
4. **Results and ledger.** The secretary learns how each worker ended and what to verify. A persistent ledger records
   every task, so the next worker picks up where the last one stopped, even after a restart.

Four rules hold throughout:

- **No extra model calls.** Every check is plain code.
- **Hermes keeps its own job.** Command approval, stale-file protection, step budgets, stall detection and completion
  delivery stay with Hermes.
- **Quiet by default.** The plugin talks only to the secretary, never to your chat, and only when something happened.
- **Never in the way.** A plugin error is logged; it never blocks a tool.

## Features

### 1. Turn budget

| Time in one turn | Effect |
|---|---|
| under 60 s | nothing |
| from 60 s | one note: answer the user or delegate within 1-2 steps |
| from 120 s | editing and execution pause; reading, `delegate_task` and wrap-up tools remain |

- A message from you mid-turn resets the clock.
- Wrap-up tools are `memory`, `todo`, `clarify`, `send_message`, and `process_manage` to list, poll, read logs or kill.
- A foreground command that runs past 30 s moves to the background through Hermes' own yield path. It keeps running,
  Hermes reports when it ends, and the secretary is told it has not failed.
- `process_manage(action='wait')` is refused: waiting on a background job would freeze the chat again.

### 2. Handoff

`delegate_task` is returned to the secretary once per turn when:

- the `context` is under 120 characters. The note asks for what is known, the exact targets, what must not change,
  when to stop and what proves success;
- it names a file another running worker is changing. The secretary can wait, steer that worker, or stop it.

A second attempt always goes through. Every accepted task then gets a briefing after the secretary's own context:

| Section | Why |
|---|---|
| Your last few messages, verbatim | Constraints like "don't touch X" reach the worker |
| `USER.md` and `MEMORY.md` | Hermes does not load memory into subagents |
| Workers running now and their files | So it leaves their files alone |
| This chat's recent unfinished tasks | Summary, blockers, corrections, changed files and log path, so it builds on them |
| Working rules | Stay in scope, stop after the same failure twice, roll back risky changes, verify the end state |
| A default `output_schema` | `status`, `summary`, `evidence`, `blockers`, unless the task brought its own |

### 3. Worker limits

When a worker runs the same tool with the same arguments and gets the same result, it is warned at the third time and
stopped at the fifth. A stopped worker's tool calls are refused and it must report `blocked`. The secretary then
hears why it was stopped and tells you in one line what got stuck and what it will do instead.

### 4. Results and task ledger

**Results.** When a worker returns, the secretary's next turn opens with a note: the evidence the worker reported, or
a warning that it claimed success without evidence, did not finish, or was stopped. A task the secretary stopped
itself is marked as such, so it is not reported as a failure.

**Ledger.** One record per task, written by the plugin's hooks:

- goal and status;
- the files it changed;
- corrections sent with `action='steer'`;
- the path of its live transcript;
- how it ended: summary, evidence, blockers.

The ledger is how work carries over. Hermes cannot resume a subagent once it ends, so the next worker in the chat is
briefed with the unfinished task's record instead. If Hermes restarts mid-task, the secretary is told which tasks were
lost, that their side effects are unknown, and which files they had touched. Notes wait in the ledger until the chat's
next turn, so a restart never drops them.

It lives in `<hermes home>/plugin-data/proactive-agent/ledger.db` and keeps each chat's last 20 finished tasks for up to
7 days.

### Scope

Only platforms in `governed_platforms` are governed (default `["weixin"]`), together with the workers they spawn.
Other platforms, cron runs, and workers spawned elsewhere are left alone.

## Configuration

Settings go under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`. Any key can be overridden with
an environment variable `PROACTIVE_<KEY>`.

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
| `governed_platforms` | `["weixin"]` | Platforms whose secretary and workers are governed |
| `turn_soft_seconds` | `60` | When the secretary is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | When editing and execution pause for the rest of the turn |
| `terminal_auto_detach_seconds` | `30` | Foreground commands longer than this move to the background (`0` disables) |
| `handoff_min_context_chars` | `120` | A shorter `context` is returned once per turn |
| `worker_repeat_limit` | `5` | Repeats of the same step that stop a worker (warned two earlier) |

## Compatibility

- Hermes Agent 0.22 or later (tested on v0.22.7).
- Python 3.10 or later.

## Development

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

`tests/test_hermes_e2e.py` drives the plugin through a real Hermes install when `hermes_cli` is importable, and is
skipped otherwise.

```
proactive-agent/      the plugin, and all that Hermes installs
├── __init__.py       registers the hooks
├── secretary.py      turn budget, background detach
├── handoff.py        delegate_task checks and briefing
├── workers.py        repeat stop, result notes
├── ledger.py         persistent task ledger
├── context.py        who is calling: secretary, worker or cron
├── prompt.py         system prompt section
├── config.py         settings
├── util.py           helpers
└── plugin.yaml       manifest
tests/                one file per module, plus end-to-end tests against Hermes
```

## License

[MIT](LICENSE)
