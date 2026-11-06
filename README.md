# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

## Answer first

**Hermes Proactive Agent is a plugin for [Hermes Agent](https://hermes-agent.nousresearch.com) that keeps a chat assistant responsive while subagents do the slow work.** It caps each main-agent turn at 60 s (a reminder to answer or delegate) and 120 s (editing and execution paused), backgrounds terminal commands past 30 s, returns `delegate_task` briefs under 120 characters, stops a subagent at its fifth identical tool result, brings back questions only you can answer, repeats each background result until you hear it, and records every task in a SQLite ledger that survives restarts. Zero model calls; it never posts into your chat.

## Install

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

Then add two settings to `~/.hermes/config.yaml` and restart the gateway:

```yaml
delegation:
  max_iterations: 60                # a subagent that needs more is usually stuck
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true # lets the plugin wake the main agent to finish a report you interrupted
```

Both are optional. Without `allow_gateway_injection`, a report you interrupted waits for your next message instead. If you installed the plugin another way, use the id `hermes plugins list` shows instead of `proactive-agent`.

MIT licensed, Python 3.10+, no runtime dependencies.

## The problem it solves

In a chat app, an agent that works for ten minutes is an agent you cannot talk to for ten minutes. Hermes already lets the main agent hand work to subagents that run in the background. What it does not do is make that hand-off behave like a good secretary: the **main agent** stays reachable, decides and reports back; **subagents** do the slow work; every job ends with a result that is checked, reaches you, and is remembered.

Six failures of ungoverned delegation, and the mechanism that answers each:

| Failure | Mechanism in this plugin |
|---|---|
| The main agent runs builds and long debugging itself, and the chat goes silent | A per-turn time budget (60 s reminder, 120 s pause on editing and execution); terminal commands past 30 s move to the background |
| A subagent starts blind: no conversation, no constraints, no idea what was tried | Briefs under 120 characters are returned; every accepted brief carries your words, memory and earlier work |
| A stuck subagent repeats the same step until its budget runs out, or guesses at a choice only you can make | Identical tool results are counted: a warning at 3, a hard stop at 5. A subagent that needs your decision, or is refused by approval or a rule, ends with a question the main agent brings to you |
| "Done" is a self-report | The main agent is shown the evidence the subagent reported, or told it has none |
| You write just as a result comes in, the main agent answers your message, and the result never reaches you | The result stays unreported until a reply you did not interrupt; it is repeated in the same turn, by waking the main agent, or with your next message |
| What a subagent did is lost when it ends or Hermes restarts | A SQLite ledger keeps every task, survives restarts and briefs the next subagent |

## Status quo vs governed

Status quo is a vanilla Hermes Agent with delegation enabled and this plugin absent.

| Dimension | Status quo | Governed by proactive-agent |
|---|---|---|
| Turn duration | Unbounded; the chat is silent until the work ends | Soft cap 60 s, hard cap 120 s |
| Tools after the hard cap | No change | `read_file`, `search_files`, `session_search`, `memory`, skill tools, `todo_list`, `clarify`, `delegate_task`, and `process_manage` (`list`/`poll`/`log`/`kill`) only |
| Long terminal command | Blocks the turn | Auto-detach at 30 s; the process keeps running and Hermes reports the end |
| Waiting on a background job | `process_manage(action='wait')` allowed | Refused, because waiting freezes the chat again |
| Brief quality gate | Any `context` accepted | Under 120 characters is returned once per turn |
| Write contention | Found by accident | A brief naming a file another subagent is changing is returned once per turn |
| Subagent starting context | Empty | Last 6 user messages verbatim, `USER.md`, `MEMORY.md`, running subagents and their files, unfinished tasks, working rules, default `output_schema` |
| Identical-result loop | Hermes warns subagents but does not stop them | Warn at 3, stop at 5, then tool calls are refused and the subagent must report `blocked` |
| A choice only you can make | The subagent cannot ask you, so it guesses or gives up | It ends with `needs_decision`, a question and options; the main agent asks you and dispatches again with your answer |
| A step refused by approval or a hook | The subagent may try another way to the same result | A note on the spot: do not work around it; if the goal needs it, report `needs_decision` |
| Result verification | "Done" is a self-report | Next turn opens with the evidence, or a warning about missing evidence, unfinished work or a repeat stop |
| A result arrives while you type | The main agent may answer only your message; the result is never mentioned | Repeated until a reply you did not interrupt; the main agent is woken to report it if needed |
| Durability | Gone when the subagent ends or the gateway restarts | SQLite ledger, last 20 finished tasks per chat, up to 7 days |
| Restart recovery | No signal | The main agent is told which tasks were lost, that side effects are unknown, and which files they touched |
| Blast radius | N/A | Exceptions are logged and never block a tool |
| Token cost | N/A | Zero model calls |
| Scope | N/A | Only `governed_platforms` (default `["weixin"]`) and their subagents; cron and other platforms untouched |

## How it works

### 1. The chat stays responsive

The main agent may think and investigate, but every turn has a budget.

| Time in one turn | Effect |
|---|---|
| under 60 s | nothing |
| from 60 s | one note: answer the user or delegate within 1-2 steps |
| from 120 s | editing and execution pause; reading, `delegate_task` and wrap-up tools remain |

- A message from you mid-turn restarts the clock, whether Hermes steers it into the turn or redirects the turn with it.
- Wrap-up tools are `memory`, the skill tools, `todo_list`, `clarify`, and `process_manage` to list, poll, read logs or kill.
- A terminal command still running after 30 s moves to the background through Hermes' own yield path. It keeps running, Hermes reports when it ends, and the main agent is told it has not failed.
- Waiting on a background job (`process_manage(action='wait')`) is refused, since it would freeze the chat again.

### 2. Subagents start informed

The main agent knows the conversation, so it writes the brief. A `delegate_task` comes back once per turn when:

- its `context` is under 120 characters. The note asks for what is known, the exact targets, what must not change, when to stop and what proves success;
- it names a file another running subagent is changing. The main agent can wait, steer that subagent, or stop it.

A second attempt always goes through. Every accepted task then gets a briefing after the main agent's own context:

| Section | Why |
|---|---|
| Your last 6 messages, verbatim | Constraints like "don't touch X" reach the subagent |
| `USER.md` and `MEMORY.md` | Hermes does not load memory into subagents |
| Subagents running now and their files | So it leaves those files alone |
| This chat's recent unfinished tasks | Summary, blockers, corrections, changed files and log path, so it builds on them |
| Working rules | Stay in scope, stop after the same failure twice, bring back choices only you can make, never work around a refusal, keep risky changes reversible, verify the end state |
| A default `output_schema` | `status` (`completed`, `partial`, `blocked`, `needs_decision` or `failed`), `summary`, `evidence`, `blockers`, and `question` and `options` for a decision, unless the task brought its own |

### 3. Subagents do not spin or guess

**It does not spin.** A subagent that runs the same tool with the same arguments and gets the same result is warned at the third time and stopped at the fifth. Repeats count over the whole run, not only back to back. Results are compared by their first line, with long numbers and hashes masked, so a fresh request id does not make a result new. Once stopped, its tool calls are refused and it must report `blocked`.

Hermes' own loop guard also notices repeats, but for subagents it only warns. Their overall step budget stays with Hermes (`delegation.max_iterations`).

**It does not guess.** A subagent cannot ask you anything: Hermes gives it no `clarify` tool and cannot resume it once it ends. So when the goal needs a choice only you can make (two workable approaches, a production change, spending, deleting data) and your words and memory do not settle it, its working rules tell it to stop and report `needs_decision` with one question and the options. Hermes checks the report against the schema and asks once more if the question is missing.

The same goes for a step that Hermes' approval or a hook refuses, or that waits for an approval nobody can give during the run. The subagent gets a note on that result (for a hook, on its next one): do not retry it or reach the same result another way; if the goal needs it, report `needs_decision`. A refusal that says the user denied it may be automatic: in some setups Hermes denies a subagent's dangerous commands without asking anyone.

The main agent is told to ask you in one short message with the options, and to dispatch a new subagent with your answer. That subagent is briefed with the earlier task's record, question included. If the main agent has not dispatched again by your next message after you heard the question, it is reminded once with that message.

### 4. Results are checked, reported and remembered

**Every result is checked.** When a subagent returns, the main agent's next turn opens with a note: the evidence it reported, or a warning that it claimed success without evidence, did not finish, or was stopped for repeating itself. In that last case the main agent tells you in one line what got stuck and what it will do instead. A task the main agent stopped itself is noted as such, not as a failure.

**Every result reaches you.** A subagent that ran in the background reports back to the main agent in a turn of its own. If you send a message while that turn runs, Hermes folds your message into it, and the main agent tends to answer only you. So a result stays marked unreported until a turn that showed it ends with a reply you did not cut into:

- if the main agent uses a tool after your message, that tool's result reminds it to answer you and report the result in the same reply;
- if it replies without a tool, the plugin wakes it once more through Hermes' message injection, and it reports the result in a short follow-up (or stays silent if it already did). Hermes queues the wake behind any running turn, so it never cuts into one;
- if Hermes interrupted the turn instead, or the wake is not allowed, your next message carries the reminder.

Writing several times does not lose it either: each message that cuts in leads to a fresh reminder. At most 2 wakes are sent per result, and a result older than a day is no longer chased.

**Every task is recorded.** The task ledger keeps one record per task: goal, status, the files it changed, corrections sent with `action='steer'`, its live transcript path, how it ended (summary, evidence, blockers, and the question it asked you) and whether you have heard the result.

**Work carries over.** Hermes cannot resume a subagent once it ends, so the next subagent in the chat is briefed with the unfinished task's record instead. If Hermes restarts mid-task, the main agent is told which tasks were lost, that their side effects are unknown, and which files they had touched. Notes and unreported results wait in the ledger, so a restart never drops them.

The ledger lives in `<hermes home>/plugin-data/proactive-agent/ledger.db` and keeps each chat's last 20 finished tasks for up to 7 days.

## Design rules

- **It talks only to the main agent.** It adds notes to the main agent's context and, to finish a report you interrupted, wakes the main agent with an internal message. It never posts to your chat; the main agent decides what you hear.
- **No timed messages.** No progress lines or timers. Every note is triggered by an event (a turn starts, a tool returns, a subagent ends, a turn ends with a result unreported) and delivered once.
- **No model calls.** Every check is plain code.
- **Hermes keeps its own job.** Command approval, stale-file protection, step budgets, loop warnings and handing finished results to the main agent stay with Hermes. The plugin only adds what Hermes leaves open.
- **Never in the way.** A plugin error is logged; it never blocks a tool.
- **Only where you ask.** Only platforms in `governed_platforms` (default `["weixin"]`) and the subagents they spawn are governed. Other platforms and cron runs are left alone.

## Configuration

Settings go under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`. Any key can be overridden with an environment variable `PROACTIVE_<KEY>`.

```yaml
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true   # Hermes' permission, see Install
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| Key | Default | Meaning |
|---|---|---|
| `governed_platforms` | `["weixin"]` | Platforms whose main agent and subagents are governed |
| `turn_soft_seconds` | `60` | When the main agent is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | When editing and execution pause for the rest of the turn |
| `terminal_auto_detach_seconds` | `30` | Terminal commands running longer than this move to the background (`0` disables) |
| `handoff_min_context_chars` | `120` | A shorter `context` is returned once per turn |
| `worker_repeat_limit` | `5` | Identical attempts that stop a subagent (warned two earlier) |

Resolution order: environment variable `PROACTIVE_<KEY>`, then plugin settings, then the default. Invalid values are logged and ignored.

## FAQ

**How do I stop a Hermes Agent chat from hanging while it works?**
Enable the plugin. The main agent is nudged at 60 s to answer or delegate and loses edit and execute tools at 120 s, so no turn can run unbounded while you wait. A message from you mid-turn restarts the clock.

**I sent a message just as a subagent finished, and the agent never told me the result. What fixes that?**
The plugin keeps that result marked unreported until a reply you did not interrupt. The main agent is reminded on its next tool result, woken for a short follow-up if it answered you without one, or reminded with your next message. Set `allow_gateway_injection: true` for the wake; see Install.

**Can a subagent ask me a question?**
Not directly: Hermes gives subagents no way to reach you. With the plugin, a subagent that needs your decision, or whose step was refused by approval or a hook, ends with status `needs_decision`, a question and options. The main agent asks you, then dispatches a new subagent with your answer, briefed with what the first one found.

**How do I stop subagents from repeating the same tool call forever?**
Identical tool, arguments and result are counted over the whole run: a warning at 3, a hard stop at 5, then tool calls are refused so the subagent must report `blocked`. Lowering `delegation.max_iterations` to about 60 catches the remaining cases earlier.

**How do I persist subagent task results across a Hermes gateway restart?**
Every task, pending note and unreported result is written to SQLite at `<hermes home>/plugin-data/proactive-agent/ledger.db`. After a restart the main agent is told which tasks were lost, that side effects are unknown, and which files they had touched; queued notes arrive on the chat's next turn.

**How do I keep a long build from blocking my chat?**
Any governed foreground terminal command still running after 30 s is detached through Hermes' yield path: it keeps running, the agent is told it has not failed and will be notified at the end. `process_manage(action='wait')` is refused so the agent cannot freeze the chat by waiting.

**Why does my subagent not know what the conversation said?**
Hermes does not load chat history or memory into subagents. This plugin appends your last messages verbatim plus `USER.md` and `MEMORY.md` to every accepted brief, and refuses briefs under 120 characters once per turn.

**How do I verify a subagent actually finished?**
Every accepted task carries a default `output_schema` of `status`, `summary`, `evidence`, `blockers`, plus `question` and `options` when it needs your decision. The main agent's next turn opens with the reported evidence, or explicitly warns about a success claim without evidence, an unfinished task, or a repeat stop.

**Does it make extra model calls or cost tokens?**
No model calls of its own. It only adds text to the main agent's context. A wake to finish an interrupted report is one extra turn of the main agent, the same cost as one message from you.

**Does it affect cron jobs, the CLI or other chat platforms?**
No. Only platforms in `governed_platforms` (default `["weixin"]`) and their subagents are governed. Cron runs and unlisted platforms are untouched.

**Does it replace Hermes' approval, file protection or loop guard?**
No. Those stay with Hermes, and the plugin fails open: a plugin error is logged and never blocks a tool. A subagent that Hermes' approval refuses is only told not to work around it and to bring the question back.

## Compatibility

- Hermes Agent 0.22 or later (tested on v0.22.7).
- Python 3.10 or later.
- CI runs `ruff check` and `pytest` on Python 3.10 through 3.14.

## Development

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

`tests/test_hermes_e2e.py` drives the plugin through a real Hermes install when `hermes_cli` is importable, and is
skipped otherwise.

```
proactive-agent/      the plugin, and all that Hermes installs
├── secretary.py      1. turn budget, background detach, user messages mid-turn
├── handoff.py        2. brief checks and briefing; 3. working rules and result schema
├── workers.py        3. subagent registry, repeat stop, notes on refused steps
├── reports.py        3. questions for you; 4. result notes, unreported results, restart recovery
├── ledger.py         4. task ledger
├── prompt.py         system prompt section for the main agent
├── context.py        who is calling: main agent, subagent or cron
├── config.py         settings
├── util.py           helpers
├── __init__.py       registers the hooks
└── plugin.yaml       manifest
tests/                one file per module, plus end-to-end tests against Hermes
```

## Machine-readable docs

- [llms.txt](llms.txt) - compact index for AI agents and answer engines ([raw](https://raw.githubusercontent.com/JerryLiu369/hermes-proactive-agent/main/llms.txt))
- [llms-full.txt](llms-full.txt) - the whole project in one file ([raw](https://raw.githubusercontent.com/JerryLiu369/hermes-proactive-agent/main/llms-full.txt))

## License

[MIT](LICENSE)
