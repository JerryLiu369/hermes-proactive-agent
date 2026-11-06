# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that runs your chat agent like a secretary with a team
of workers.

In a chat app, an agent that works for ten minutes is an agent you cannot talk to for ten minutes. Hermes already lets
the main agent hand work to subagents that run in the background. This plugin makes that hand-off work the way a good
secretary would: the **main agent** stays reachable, decides and reports back; **subagents** do the slow work; and
every job ends with a result that is checked and remembered.

## Install

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

Restart the gateway afterwards; upgrade with `hermes plugins update proactive-agent`. Also recommended: a subagent
that needs more than about 60 model calls is usually stuck, so lower Hermes' step budget and it reports sooner.

```yaml
delegation:
  max_iterations: 60
```

## Why

Five things go wrong when a chat agent delegates on its own. The plugin answers each one.

| Without the plugin | With it |
|---|---|
| The main agent runs builds and long debugging itself, and the chat goes silent | Each turn has a time budget, and long commands move to the background |
| A subagent starts blind: no conversation, no constraints, no idea what was tried | A thin brief is sent back, and every brief carries your words, memory and earlier work |
| A stuck subagent repeats the same step until its budget runs out | It is stopped after five identical attempts |
| "Done" is a self-report | The main agent is told what evidence came back and to check it |
| Once a subagent ends, or Hermes restarts, what it did is gone | A task ledger keeps every task and briefs the next subagent |

## What it does

### 1. The chat stays responsive

The main agent may think and investigate, but every turn has a budget.

| Time in one turn | Effect |
|---|---|
| under 60 s | nothing |
| from 60 s | one note: answer the user or delegate within 1-2 steps |
| from 120 s | editing and execution pause; reading, `delegate_task` and wrap-up tools remain |

- A message from you mid-turn restarts the clock.
- Wrap-up tools are `memory`, the skill tools, `todo_list`, `clarify`, and `process_manage` to list, poll, read logs or
  kill.
- A terminal command still running after 30 s moves to the background through Hermes' own yield path. It keeps
  running, Hermes reports when it ends, and the main agent is told it has not failed.
- Waiting on a background job (`process_manage(action='wait')`) is refused, since it would freeze the chat again.

### 2. Subagents start informed

The main agent knows the conversation, so it writes the brief. A `delegate_task` comes back once per turn when:

- its `context` is under 120 characters. The note asks for what is known, the exact targets, what must not change,
  when to stop and what proves success;
- it names a file another running subagent is changing. The main agent can wait, steer that subagent, or stop it.

A second attempt always goes through. Every accepted task then gets a briefing after the main agent's own context:

| Section | Why |
|---|---|
| Your last few messages, verbatim | Constraints like "don't touch X" reach the subagent |
| `USER.md` and `MEMORY.md` | Hermes does not load memory into subagents |
| Subagents running now and their files | So it leaves those files alone |
| This chat's recent unfinished tasks | Summary, blockers, corrections, changed files and log path, so it builds on them |
| Working rules | Stay in scope, stop after the same failure twice, keep risky changes reversible, verify the end state |
| A default `output_schema` | `status`, `summary`, `evidence`, `blockers`, unless the task brought its own |

### 3. Subagents do not spin

A subagent that runs the same tool with the same arguments and gets the same result is warned at the third time and
stopped at the fifth. From then on its tool calls are refused and it must report `blocked`. Its overall step budget
stays with Hermes (`delegation.max_iterations`).

### 4. Results are checked and remembered

**Every result is reviewed.** When a subagent returns, the main agent's next turn opens with a note: the evidence it
reported, or a warning that it claimed success without evidence, did not finish, or was stopped for repeating itself.
In that last case the main agent tells you in one line what got stuck and what it will do instead. A task the main
agent stopped itself is noted as such, not as a failure.

**Every task is recorded.** The task ledger keeps one record per task: goal, status, the files it changed, corrections
sent with `action='steer'`, its live transcript path, and how it ended (summary, evidence, blockers).

**Work carries over.** Hermes cannot resume a subagent once it ends, so the next subagent in the chat is briefed with
the unfinished task's record instead. If Hermes restarts mid-task, the main agent is told which tasks were lost, that
their side effects are unknown, and which files they had touched. Notes wait in the ledger until the chat's next turn,
so a restart never drops them.

The ledger lives in `<hermes home>/plugin-data/proactive-agent/ledger.db` and keeps each chat's last 20 finished tasks
for up to 7 days.

## Design rules

- **It talks only to the main agent.** It adds notes to the main agent's context and never posts to your chat.
- **No model calls.** Every check is plain code.
- **Hermes keeps its own job.** Command approval, stale-file protection, step budgets, stall detection and delivery
  of finished results stay with Hermes.
- **Never in the way.** A plugin error is logged; it never blocks a tool.
- **Only where you ask.** Only platforms in `governed_platforms` (default `["weixin"]`) and the subagents they spawn
  are governed. Other platforms and cron runs are left alone.

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
| `governed_platforms` | `["weixin"]` | Platforms whose main agent and subagents are governed |
| `turn_soft_seconds` | `60` | When the main agent is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | When editing and execution pause for the rest of the turn |
| `terminal_auto_detach_seconds` | `30` | Terminal commands running longer than this move to the background (`0` disables) |
| `handoff_min_context_chars` | `120` | A shorter `context` is returned once per turn |
| `worker_repeat_limit` | `5` | Identical attempts that stop a subagent (warned two earlier) |

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
├── secretary.py      1. turn budget, background detach
├── handoff.py        2. brief checks and briefing
├── workers.py        3. repeat stop, 4. result notes
├── ledger.py         4. task ledger
├── prompt.py         system prompt section for the main agent
├── context.py        who is calling: main agent, subagent or cron
├── config.py         settings
├── util.py           helpers
├── __init__.py       registers the hooks
└── plugin.yaml       manifest
tests/                one file per module, plus end-to-end tests against Hermes
```

## License

[MIT](LICENSE)
