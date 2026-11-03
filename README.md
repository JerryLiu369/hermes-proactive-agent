# Hermes Proactive Agent Governor (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-2.1.0-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

**Hermes Proactive Agent Governor** (`proactive-agent`) is an execution governance plugin for [Hermes Agent](https://hermes-agent.nousresearch.com). It enforces proactive orchestrator discipline, eliminates blocking foreground commands, prevents infinite serial tool loops, and provides resilient self-healing when humans intervene mid-turn.

## 🎯 Core Philosophy: "All Heavy Tasks Asynchronous"

Autonomous LLM agents easily fall into traps: freezing chat for minutes on long bash commands, running in endless monolithic trial-and-error loops within a single turn, or ignoring mid-flight user steering.

`proactive-agent` transforms your agent into a disciplined, proactive orchestrator:
1. **Reply first, then delegate**: Provide immediate feedback to keep conversations snappy and responsive.
2. **Embrace background execution & delegation**: Long-running commands run detached; multi-step goals fan out to subagents via `delegate_task`.
3. **Quiet execution**: Keep execution clean without spamming internal PIDs or raw terminal logs into user chat.
4. **Verify before claiming done**: Check physical artifacts on disk before declaring completion.

---

## ⚡ Key Pain Points Solved

### 1. Adaptive Terminal Detach (>30s) — Never Freeze Foreground Chat
- **The Problem**: When an agent runs build scripts (`cargo build`, `npm install`), test suites, or training processes in the foreground, the terminal blocks. Chat freezes for minutes, risking gateway timeouts or connection drops.
- **The Solution**: 
  - Non-invasively monitors `BaseEnvironment._wait_for_process` without breaking native interrupt wiring.
  - If a foreground command exceeds `terminal_auto_detach_seconds` (default: `30s`), it triggers Hermes's native `yield_handler` to adopt the running process into `process_registry` with zero kill and `notify_on_complete=True`.
  - Replaces tool result notes via `transform_tool_result` to instruct the LLM: *"Command running asynchronously in the background. Conclude your turn immediately and release the chat!"*

### 2. Turn Circuit Breaker (>5 rounds / >100s) — Break Monolithic Death Loops
- **The Problem**: Single-threaded agents often get stuck in endless trial-and-error loops (10+ serial bash/patch tool calls in a single response turn), burning tokens, degrading context quality, and locking the UI.
- **The Solution**:
  - **Batch-Level Round Counting**: Parallel tool calls emitted within a single assistant step share one round counter (`pre_api_request`), preventing premature tripping.
  - **Hard Circuit Breaker**: If serial rounds exceed `turn_max_tool_rounds` (default: `5`) or turn duration exceeds `turn_max_duration_seconds` (default: `100s`), heavy monolithic tools (`terminal`, `patch`, `write_file`, `execute_code`) are frozen.
  - **Meta-Tool Whitelist**: Lightweight meta-tools remain completely accessible:
    - `delegate_task`: Fan out heavy sub-tasks to isolated subagents in parallel.
    - `skill_manage` / `memory`: Perform atomic self-evolution and retain learnings without action distortion.
    - `skill_view` / `skills_list`: Read existing guidelines.
  - **Subagent Bypass Guard**: Child subagents (`task_id.startswith("sa-")`) bypass the breaker to execute subtasks cleanly.

### 3. Human Intervention & Steer Self-Healing — Dynamic Quota Reset
- **The Problem**: Static quota systems freeze agents permanently when users jump in mid-flight to correct direction.
- **The Solution**:
  - Monitors `display_kind == "steer"` and `[OUT-OF-BAND USER MESSAGE]` markers in `pre_api_request` and `pre_llm_call`.
  - The moment a human intervenes (either in a new turn or out-of-band mid-turn), round counters and elapsed timers are instantly reset to zero. The agent is granted a fresh quota to execute the user's updated directive.
  - Automatically prunes stale turn states to guarantee zero memory leaks across sessions.

### 4. Dynamic Prompt Discipline
- Dynamically renders and registers prompt context (`proactive-agent.discipline`) matching the active configuration into the agent's system prompt.

---

## ⚙️ Configuration Reference

Parameters can be configured in your Hermes profile config (`config.yaml`), plugin settings, or environment variables:

| Config Key | Env Var | Type | Default | Description |
|---|---|---|---|---|
| `terminal_auto_detach_seconds` | `PROACTIVE_TERMINAL_AUTO_DETACH_SECONDS` | `int` | `30` | Max seconds a foreground terminal command can run before being detached into the background. |
| `turn_max_tool_rounds` | `PROACTIVE_TURN_MAX_TOOL_ROUNDS` | `int` | `5` | Max serial tool rounds in the main agent turn before circuit breaker restricts execution. |
| `turn_max_duration_seconds` | `PROACTIVE_TURN_MAX_DURATION_SECONDS` | `int` | `100` | Max cumulative tool execution duration (seconds) in the main agent turn before circuit breaker trips. |

### Example `config.yaml`
```yaml
plugins:
  entries:
    proactive-agent:
      enabled: true
      config:
        terminal_auto_detach_seconds: 30
        turn_max_tool_rounds: 5
        turn_max_duration_seconds: 100
```

---

## 🚀 Installation & Usage

### Install via Hermes CLI
```bash
# Install plugin directly from GitHub
hermes plugins install github:JerryLiu369/hermes-proactive-agent

# Enable plugin
hermes plugins enable proactive-agent

# Verify plugin status
hermes plugins list
```

### Uninstall
```bash
hermes plugins disable proactive-agent
hermes plugins uninstall proactive-agent
```

---

## 🛠️ Environment Compatibility

- **Hermes Agent**: v0.8.0 and above
- **Python**: 3.10+
- **OS**: Linux, macOS, WSL

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
