# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

A plugin for [Hermes Agent](https://hermes-agent.nousresearch.com) that makes your chat assistant work like a good secretary: the **main agent** stays reachable in the chat while **subagents** handle the slow work in the background, and every delegated job ends with a result that is verified, delivered to you, and recorded.

Built for talking to Hermes from a messaging app, the plugin runs as plain Python code. It makes no model calls of its own and never writes directly into your chat; every message you read still comes from the main agent.

## Install

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

Two optional settings in `~/.hermes/config.yaml` work well alongside the plugin. Restart the gateway after changing them:

```yaml
delegation:
  max_iterations: 60                # a subagent that needs more steps is usually stuck
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true # lets the plugin wake the main agent to finish a report you interrupted
```

Without `allow_gateway_injection`, an interrupted report simply waits for your next message. If you installed the plugin under a different name, replace `proactive-agent` with the id shown by `hermes plugins list`.

## Why

Hermes already knows how to delegate background work to subagents. In a live chat, however, six things routinely go wrong:

- **The chat goes silent.** The main agent starts a build or a long debugging session itself and stops replying for ten minutes.
- **Subagents start blind.** A subagent cannot see the conversation or memory, so it has no idea what you asked for, what was already tried, or what it must leave alone.
- **Stuck subagents spin or guess.** A subagent runs the same failing command over and over until its step budget runs out, or hits a decision that is not its to make and guesses.
- **"Done" is taken on faith.** A subagent says it finished, and the main agent repeats the claim without checking any proof.
- **Results get buried when you type.** A background result arrives right as you send a new message. The main agent answers your message and never mentions the finished job.
- **Context vanishes on exit or restart.** Once a subagent finishes, or when the Hermes gateway restarts mid-task, there is no record of what it found or which files it touched.

The next four sections explain how the plugin solves each of these problems.

## What it does

### The chat stays responsive

Every turn of the main agent has a time budget:

- **At 60 seconds**, the main agent gets a single reminder to answer you or hand the remaining work to `delegate_task` within a step or two.
- **At 120 seconds**, editing files and running commands are paused for the rest of the turn. The main agent can still read files, look at images (`vision_analyze`), search, delegate tasks, and wrap up using `memory`, the skill tools, `todo_list`, `clarify`, or `process_manage` (`list`, `poll`, `log`, `kill`).
- **When you write mid-turn**, the clock resets so the agent has fresh time to handle your new input.
- **Context compression does not count.** When Hermes compresses the conversation inside a turn, the clock stops until the summary is done, so a long compression cannot push the agent into the 120-second pause on its own.

Long commands cannot hold a turn hostage either. Any foreground terminal command still running after **30 seconds** is moved to the background through Hermes' own yield mechanism (on Hermes' local terminal backend, the only one that can hand a running process over). The process keeps running, Hermes notifies the main agent when it finishes, and a note tells the main agent that the command is still running rather than failed. Calling `process_manage(action='wait')` is refused so the main agent cannot freeze the chat by blocking on a background job.

### Subagents start informed

Because a subagent cannot see the chat history or memory, the main agent has to brief it. Before a `delegate_task` call goes through, the plugin checks the brief and returns it **once per turn** in two cases:

- its `context` is shorter than **120 characters**, with a note listing what to include (what is known, exact target paths or URLs, what must not change, when to stop, and what proves success);
- it names a file that another running subagent is already modifying, so the two workers do not overwrite each other.

A second attempt in the same turn always passes. Every accepted task then gets a structured briefing appended after the main agent's own instructions:

- **Your last 6 messages**, word for word, so constraints like "don't touch X" arrive intact.
- **`USER.md` and `MEMORY.md`**, which Hermes skips when spawning subagents. They are copied in full, so whatever they hold about you, personal details included, is visible to every governed subagent.
- **Other running subagents** in this chat and the files they are currently changing.
- **Recent unfinished tasks** from this chat (what they found, what blocked them, any question they asked, mid-run `steer` corrections, files they modified, and their full transcript path), so a follow-up worker picks up where the earlier one left off.
- **Working rules**: stay in scope, stop after the same step fails twice, bring back choices instead of guessing, never work around a refused command, require a rollback path for risky changes, leave other workers' files alone, and verify the real end state before reporting success.
- **How to report**: end with a JSON object holding `status` (`completed`, `partial`, `blocked`, `needs_decision`, or `failed`), `summary`, `evidence`, and `blockers`, plus `question` and `options` when a decision is needed. This is asked for, not enforced: the plugin sets no `output_schema`, so Hermes needs no `jsonschema` and never makes a subagent rewrite its answer. The plugin reads the JSON when it is there, also inside a fenced block; a report in plain prose that says `needs_decision` still counts as a decision, and anything else is left to the main agent to read. A task that sets its own `output_schema` keeps it.

### Subagents do not spin or guess

Hermes warns subagents when they repeat tool calls, but never stops them. The plugin tracks every tool call across the subagent's entire run, not only consecutive steps. Results are compared by their first line with long numbers and hashes masked out, so a fresh timestamp or request ID still counts as the same outcome. When a subagent runs the same tool with the same arguments and gets the same result:

- on the **3rd time**, it receives a warning to change approach or report the blocker;
- on the **5th time**, it is stopped: further tool calls are refused and it must finish with status `blocked`.

The overall step ceiling stays with Hermes (`delegation.max_iterations`).

Subagents also have no way to ask you a question directly: Hermes gives them no `clarify` tool and cannot resume a subagent once it exits. Instead of guessing, a subagent stops and reports status `needs_decision` with one clear `question` and the available `options` whenever:

- **It reaches a choice that is not its to make** (such as choosing between two valid designs, changing production, spending money, deleting data, or making a risky change without a rollback path) and its brief does not settle the question. If it forgets the `question`, the main agent is told to read its summary instead.
- **A step is refused by command approval or a hook**, or is left waiting for an approval nobody can grant during the run. The subagent gets an immediate note (or a note on its next tool result when blocked by a hook) telling it not to retry the command or look for a workaround, and to report `needs_decision` instead. Because some Hermes setups automatically deny dangerous subagent commands with a "user denied" message, the note also reminds the subagent not to assume you personally rejected it.

When a `needs_decision` report comes back, the main agent has the full chat context. If your earlier messages, memory, or facts it can check already answer the question, the main agent settles it on its own; if it truly is your call, it asks you in one short message with the options. Either way, once the answer is known, the main agent spawns a new subagent with the decision and the earlier task's record. If you have heard the question and the main agent still has not dispatched a follow-up by your next message, that turn reminds it once.

### Results are checked, reach you, and are remembered

When a subagent finishes, the main agent's next turn opens with a verification note:

- if the subagent reported `completed` with evidence, the note lists that evidence and tells the main agent to spot-check the real state before telling you;
- if it claimed success without evidence, left work unfinished, or was stopped for repeating itself, the note flags it; on a repeat stop, it tells the main agent to explain in one line what got stuck and what it will do instead;
- if the main agent intentionally stopped the worker with `action='stop'`, it is noted as stopped rather than failed.

Background subagents deliver their results in an internal turn of their own. If you send a message while that turn is running, Hermes merges your message into the turn, and the main agent often answers only your new message and forgets the finished background job. To make sure no result is lost, the plugin keeps every background result marked as **unreported** until a turn that showed it finishes with a reply you did not interrupt:

- **If the main agent calls a tool after your message**, that tool's result reminds it to answer you and report the finished background job in the same reply.
- **If it replies without calling a tool**, the plugin wakes the main agent once through Hermes' message injection (queued safely behind any running turn) so it can send a brief follow-up, or reply `[SILENT]` if its previous message already covered the result.
- **If Hermes interrupted the turn outright, or waking is disabled**, the reminder is attached to your next message.

Each new message you send in the meantime triggers a fresh reminder, so sending several messages in a row never buries a result. A result triggers at most **2 wakes**, and results older than **24 hours** are no longer chased.

Finally, every delegated task is saved in a SQLite ledger at `<hermes home>/plugin-data/proactive-agent/ledger.db`: its goal and status, modified files, `action='steer'` corrections, live transcript path, final report (`summary`, `evidence`, `blockers`, `question`, `options`), and whether the result has reached you. Because Hermes cannot resume a finished subagent, the next subagent spawned in the same chat automatically receives the unfinished task's record in its briefing. If the Hermes gateway restarts while subagents are running, the plugin marks those tasks as lost and tells the main agent on the next turn which tasks were interrupted, which files they had touched, and that their side effects are unknown. Pending notes and unreported results live in the ledger too, so a restart never drops them. The ledger keeps the last **20 finished tasks** per chat for up to **7 days**.

## Principles

- **Talk only to the main agent.** The plugin adds notes to the main agent's context and, when a report was interrupted, wakes it with an internal message. It never posts directly to your chat; the main agent decides what you hear.
- **Nothing runs on a timer.** There are no periodic heartbeats or progress messages. Every note is triggered by a concrete event (a turn starts, a tool returns, a subagent finishes, or a turn ends with a result still unreported) and is delivered once.
- **No model calls.** Every check is deterministic Python code with zero token cost of its own.
- **Hermes keeps its own jobs.** Command approvals, stale-file protection, total step budgets, loop warnings, and delivering background results to the main agent all stay with Hermes. The plugin only fills the gaps Hermes leaves open.
- **Public interfaces only.** The plugin works through Hermes' hooks, middleware and plugin APIs. It never patches Hermes' own code, so it passes the plugin catalog's admission check and does not collide with other plugins.
- **Never get in the way.** Every hook is wrapped in a guard: if the plugin ever raises an unexpected error, it logs the failure and lets the tool call proceed.
- **Act only where asked.** Only platforms listed in `governed_platforms` (default `["weixin"]`) and the subagents they spawn are governed. Cron jobs, the CLI, and other platforms are untouched.

## Settings

Settings live under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true   # Hermes host permission; see Install
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| Key | Default | What it does |
|---|---|---|
| `governed_platforms` | `["weixin"]` | Platforms whose main agent, and the subagents it spawns, are governed |
| `turn_soft_seconds` | `60` | Seconds before the main agent is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | Seconds before editing and command execution pause for the rest of the turn |
| `terminal_auto_detach_seconds` | `30` | Seconds before a foreground terminal command moves to the background (`0` disables) |
| `handoff_min_context_chars` | `120` | Minimum `context` length for `delegate_task`; shorter briefs are returned once per turn |
| `worker_repeat_limit` | `5` | Identical tool outcomes before a subagent is stopped (warned two attempts earlier) |

Every setting can also be overridden with an environment variable named `PROACTIVE_<KEY>` (for example, `PROACTIVE_TURN_SOFT_SECONDS=45`), which takes precedence over `config.yaml`. Invalid values are logged and ignored.

## FAQ

**Does the plugin cost extra tokens?**
It makes no model calls of its own; it only attaches compact notes to the main agent's context and to subagent briefings. Waking the main agent to finish a report you interrupted runs one extra turn, which costs roughly the same as sending one more chat message.

**Will it change how cron jobs, the CLI, or other chat platforms behave?**
No. Only platforms listed in `governed_platforms` (and the subagents spawned from them) are governed. Cron jobs, CLI sessions, and unlisted platforms run untouched.

**Does it replace Hermes' command approvals or loop guard?**
No. Both continue working as usual. When Hermes' approval blocks a subagent command, the plugin simply instructs the subagent not to look for a workaround and to bring the question back to the main agent.

**How do I stop a chat from freezing during a long command or a multi-step job?**
Install and enable `proactive-agent`. At 60 seconds the main agent is reminded to reply or hand the remaining work to a subagent; at 120 seconds it loses the tools that edit files and run commands for the rest of the turn, though reading, searching and delegating still work. A terminal command still running after 30 seconds moves to the background, and waiting on a background job is refused, so a long build cannot hold the chat.

**Why does a `delegate_task` subagent ignore `MEMORY.md` and what we just said?**
Hermes spawns subagents with no chat history and does not load their user or memory files, so there is nothing for them to read. The plugin appends a briefing to every accepted task: your last 6 messages word for word, `USER.md` and `MEMORY.md`, the other running subagents and the files they are changing, this chat's recent unfinished tasks, a few working rules, and how to report.

**What does a subagent do when it reaches a choice that is not its to make, or when its command is refused?**
It cannot ask you: Hermes gives subagents no `clarify` tool and cannot resume one that has ended. So it stops and reports `status: needs_decision` with one `question` and the `options`, and a step refused by approval or a hook is reported the same way, after a note telling it not to retry or work around the refusal. The main agent answers what the chat or memory settles, asks you in one short message when the call really is yours, then sends a new subagent with the answer.

**What happens if I write something just as a background subagent finishes?**
Hermes folds your message into the turn that is reporting the result, and the main agent tends to answer only you. The plugin keeps the result marked `unreported` until a turn that showed it ends with a reply you did not interrupt: the next tool result carries a reminder, or the plugin wakes the main agent once through Hermes' message injection (which needs `allow_gateway_injection: true`) for a short follow-up or `[SILENT]`, and failing both, your next message carries the reminder.

## Compatibility

- **Hermes Agent** 0.22 or later (verified against v0.22.7).
- Moving long commands to the background needs Hermes' default `plugins.isolation: in_process`. With `host`, commands simply stay in the foreground.
- **Python** 3.10 or later, with zero runtime dependencies.
- **CI** runs `ruff check` and `pytest` across Python 3.10 through 3.14.

## Development

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

When `hermes_cli` is importable, `tests/test_hermes_e2e.py` loads and exercises the plugin through Hermes' real plugin loader; otherwise those end-to-end tests are skipped automatically.

```text
proactive-agent/      installed plugin package (the only directory Hermes copies)
├── secretary.py      turn time budget, 30s background detach, mid-turn user messages
├── handoff.py        brief checks, context briefing, working rules, how to report
├── workers.py        running subagent registry, repeat stop, notes on refused calls
├── reports.py        result verification notes, open questions, unreported results, restart recovery
├── ledger.py         SQLite task ledger and persistent note queue
├── prompt.py         system prompt section for the governed main agent
├── context.py        caller detection (governed main agent, subagent, or cron)
├── config.py         settings resolution and defaults
├── util.py           text, JSON, and signature helpers
├── __init__.py       hook registration and routing
└── plugin.yaml       Hermes plugin manifest and config schema
tests/                unit tests per module plus end-to-end tests against Hermes
```

## For AI agents

[llms.txt](llms.txt) provides a compact index for AI agents and answer engines, and [llms-full.txt](llms-full.txt) contains the complete self-contained reference in a single file.

## License

[MIT](LICENSE)
