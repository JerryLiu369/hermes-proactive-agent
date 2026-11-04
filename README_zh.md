# Hermes Proactive Agent Governor (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.1.1-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

**Hermes Proactive Agent Governor** (`proactive-agent`) 是专为 [Hermes Agent](https://hermes-agent.nousresearch.com) 设计的执行治理与自我约束插件。它从根源杜绝前台长命令卡死终端、打破单轮大模型硬跑单体工具的死循环，并在用户中途插话时提供精准容错自愈能力。

## 🎯 核心治理哲学：重任务通通异步 (All Heavy Tasks Asynchronous)

自主大模型 Agent 在日常执行任务中最容易陷入以下失控陷阱：
- **前台卡死**：执行编译、拉取镜像、微调等耗时任务时一直卡在前台等待，导致与用户的聊天窗口彻底卡死，触发网关超时；
- **连环试错死循环**：遇到报错时在单轮内陷入盲目的“单体工具连环试错”（接连执行 10~15 轮 `terminal` / `patch`），烧光上下文并最终超时；
- **死板配额锁死**：用户中途发消息纠偏，Agent 却受死板配额限制拒绝执行。

`proactive-agent` 树立四大执行支柱：
1. **先回执后委派 (Reply first, then delegate)**：遇到复杂非平凡任务，首轮简短回执用户并释放交互，绝不在主线程单体硬跑或 sleep 等待。
2. **全面拥抱后台挂起与子代理委派 (Background execution & delegation)**：耗时任务主动或自适应挂后台；多步骤目标通过 `delegate_task` 并发分发给隔离子代理。
3. **静默执行 (Quiet execution)**：不在对话中复读内部 PID、执行器 ID 或裸命令。
4. **物料物理核验 (Verify before claiming done)**：交付前核验磁盘物料与真实执行结果。

---

## 🏛️ 系统架构与执行流转图

```text
+----------------------------------------------------------------------------------------------------+
|                                    Hermes Proactive Agent Governor                                 |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|   +-----------------------+                                                                        |
|   |   用户发起请求 / 轮次   |                                                                        |
|   +-----------+-----------+                                                                        |
|               |                                                                                    |
|               v                                                                                    |
|   +-----------------------+              用户中途介入纠偏 (Steer / 插话)                             |
|   |  模型思考与意图生成     |<....................................................................+   |
|   +-----------+-----------+                                                                     :   |
|               |                                                                                 :   |
|       +-------+-------+                                                                         :   |
|       |               |                                                                         :   |
|       v (Terminal长命令)v (常规单体工具)                                                          :   |
|  +---------+    +----------------------------+                                                  :   |
|  | 前台执行|    |       熔断器检测逻辑        |                                                  :   |
|  | 监控计时|    |   - 单轮串行轮次 > 5 轮?   |                                                  :   |
|  +----+----+    |   - 单轮累计耗时 > 90 秒?  |                                                  :   |
|       |         +--------------+-------------+                                                  :   |
|       |                        |                                                                :   |
|   [ > 30s? ]            +------+------+                                                         :   |
|    /     \              |             |                                                         :   |
|   是      否           未超限        触发熔断 (>5轮 / >90s)                                     :   |
|   |        |            |             |                                                         :   |
|   v        v            v             v                                                         :   |
| [挂起后台] [正常完成]  [执行工具]   [冻结重型单体执行工具]                                       :   |
| (零杀进程) (回显结果)   (累计耗时)   (terminal/patch/write)                                     :   |
|   |                     |             |                                                         :   |
|   |                     |             v                                                         :   |
|   |                     |     [强制要求 delegate_task                                           :   |
|   |                     |      拆解并委派隔离子代理]                                            :   |
|   |                     |             |                                                         :   |
|   +------------+--------+             |                                                         :   |
|                |                      |                                                         :   |
|                v                      v                                                         :   |
|       +------------------------------------+                                                    :   |
|       |   原子状态跟踪器与动态注入治理器   |                                                    :   |
|       |  (Prompt 注入 / 配额守护 / 内存防漏)|<...................................................+   |
|       +-----------------+------------------+                                                        |
|                         |                                                                           |
|                         v                                                                           |
|       +------------------------------------+                                                        |
|       |      即刻交还轮次，保持交互丝滑响应   |                                                        |
|       +------------------------------------+                                                        |
+----------------------------------------------------------------------------------------------------+
```

---

## ⚡ 解决三大 Agent 核心痛点与治理机制

### 痛点一：前台长命令卡死终端与超时 (Adaptive Terminal Detach > 30s)
- **痛点分析**：大模型使用前台 `terminal` 运行长命令（如编译项目、训练脚本、大文件下载）时，终端会阻塞数分钟直至超时爆掉，期间用户无法在 IM/CLI 得到任何响应。如果直接粗暴重写 `terminal` 工具，又会破坏 Hermes 原生线程中断与消息接入协议。
- **治理机制**：
  - 非侵入式挂接底层 `BaseEnvironment._wait_for_process`，无缝接入原生 `yield_handler`。
  - 一旦前台命令执行超过设定阈值（默认 `30s`），在**不杀进程**的前提下，将正在运行的 `Popen` 进程平滑过户至后台 `process_registry`，标记完成自动推送通知（`notify_on_complete=True`）。
  - 自动通过 `transform_tool_result` 注入指引，明确告知模型：“该命令已转入后台异步运行，切勿 polling / sleep-wait，立即向用户回复并释放对话轮次！”
  - **子代理与 Cron 任务彻底豁免脱壳**：子代理（`task_id.startswith("sa-")`，`platform="subagent"`）与无人值守定时任务（`platform="cron"`，`task_id.startswith("cron:")`，`HERMES_CRON_SESSION`）完全跳过 30s 定时器，保证隔离子任务与后台批处理流水线长期执行不受脱壳打断。

### 痛点二：单轮连续硬跑单体工具死循环 (Turn Circuit Breaker > 5 轮 / > 90s)
- **痛点分析**：Agent 在面对难以解决的代码报错时，容易陷入“修改 -> 报错 -> 修改 -> 报错”的单体死循环。
- **治理机制**：
  - **并发工具合并计轮**：现代 LLM 单次回复常并发吐出 2~4 个工具调用，插件在 `pre_api_request` 维度计轮，确保一次模型思考只算一轮，绝不误触熔断。
  - **硬熔断限制**：当主轮次工具调用达到第 6 轮（`> 5` 轮），或累计工具执行耗时达到 `90s` 时，立刻熔断重型单体执行工具（`terminal`, `patch`, `write_file`, `execute_code`）。
  - **元认知与自进化工具白名单放行**：
    - `delegate_task`：强制要求模型拆解任务，分发给隔离子代理并行处理；
    - `skill_manage` / `memory`：允许模型在熔断状态下沉淀经验与教训，杜绝“为存经验不得不派子代理”的动作形变；
    - `skill_view` / `skills_list`：允许检索规则。
  - **子代理与 Cron 任务彻底豁免放行**：子代理（`task_id.startswith("sa-")`）与无人值守定时任务（`platform="cron"`，`task_id.startswith("cron:")`，`HERMES_CRON_SESSION`）拥有完全的自治执行权限，自动绕过熔断，避免自动化长流程被误杀。

### 痛点三：用户中途介入插话容错自愈 (Human Intervention & Steer Self-Healing)
- **痛点分析**：若熔断配额过于死板，当用户在单轮中途发送纠偏消息（Steer Message）时，Agent 会因为配额耗尽而直接拒止。
- **治理机制**：
  - 精准嗅探消息列表中的 `display_kind == "steer"` 及 `[OUT-OF-BAND USER MESSAGE]` 标记。
  - 一旦检测到人类中途介入或开启新轮次，立即原子级重置本轮 round_count 与耗时计数器，给予 Agent 全新配额以遵从用户的最新指示。
  - 会话重置（`on_session_reset`）、会话结束（`on_session_end`）与中断（`agent_loop_stopped`）时自动清理状态，杜绝内存泄漏。

### 动态系统提示词注入与后台自治任务零注入
- 自动根据当前生效的阈值参数，将行为规范（`proactive-agent.discipline`）动态编译并注入到 Hermes 主对话系统提示词中。
- **子代理与 Cron 彻底零注入**：当处于子代理或 Cron 定时任务会话（`platform in ("subagent", "cron")`，`session_id.startswith("cron_")`，`HERMES_CRON_SESSION`）时，提示词生成器直接返回空字符串 `""`，不产生任何额外 token 开销，绝不混淆后台自动化任务的角色定位。

```text
Role: Main dialogue orchestrator. Stay responsive; heavy execution never blocks the chat turn.

1. Triage & Dispatch-Then-Yield
- LIGHT (read files, status checks, quick probes): finish in <=5 tool rounds, then answer.
- HEAVY (multi-step edits, refactors, builds, test suites, training, commands likely >30s):
  1. 仅做必要探查，尽量尽早派发；但必须在满 {max_rounds} 轮工具或 {max_duration}s 耗时硬上限前完成派发:
     - Complex reasoning or code tasks: fan out via delegate_task.
     - Long shell commands: run terminal(background=True, notify=True).
  2. Release turn immediately: output a concise one-line status to the user and end your turn. NEVER run sleep or poll.

2. Hardware Governance Limits (Main Thread Only)
- Foreground command >30s: auto-detached to background with completion notification. Do not wait; inform the user and end your turn.
- At tool round 6 (round >=6) or >90s turn time: terminal, patch, write_file and execution tools are frozen. Only delegate_task, memory, skill_manage, skill_view, skills_list remain. Do not retry; delegate remaining work or deliver current state.

3. Event Wakeup & Physical Verification
- Asynchronous tasks wake the conversation automatically upon completion.
- Verify physical on-disk artifacts and real exit codes before answering. Deliver verified results directly — never make the user ask "is it done?".
- Never expose internal PIDs, session_ids, or raw commands. Keep output clean.
```

---

## ⚙️ 参数配置说明

可在 Hermes 配置文件 `config.yaml` 或系统环境变量中自定义参数：

| 配置项 | 对应环境变量 | 类型 | 默认值 | 作用说明 |
|---|---|---|---|---|
| `terminal_auto_detach_seconds` | `PROACTIVE_TERMINAL_AUTO_DETACH_SECONDS` | `int` | `30` | 前台命令最大允许执行秒数，超过后自动安全挂起至后台运行 |
| `turn_max_tool_rounds` | `PROACTIVE_TURN_MAX_TOOL_ROUNDS` | `int` | `5` | 主代理单轮最大串行工具调用轮数，超过后触发重任务熔断 |
| `turn_max_duration_seconds` | `PROACTIVE_TURN_MAX_DURATION_SECONDS` | `int` | `90` | 主代理单轮工具执行累计最大秒数，超过后触发熔断 |

### 配置示例 `config.yaml`
```yaml
plugins:
  entries:
    proactive-agent:
      enabled: true
      config:
        terminal_auto_detach_seconds: 30
        turn_max_tool_rounds: 5
        turn_max_duration_seconds: 90
```

---

## 🚀 安装与使用

### 通过 Hermes CLI 安装
```bash
# 从 GitHub 仓库一键安装
hermes plugins install github:JerryLiu369/hermes-proactive-agent

# 启用插件
hermes plugins enable proactive-agent

# 检查已安装插件列表与状态
hermes plugins list
```

### 手动安装
```bash
git clone https://github.com/JerryLiu369/hermes-proactive-agent.git ~/.hermes/plugins/proactive-agent
hermes plugins enable proactive-agent
```

### 卸载
```bash
hermes plugins disable proactive-agent
hermes plugins uninstall proactive-agent
```

---

## 🔍 常见排障与 FAQ

| 现象 / 疑问 | 触发机制与原因 | 建议排障与操作 |
|---|---|---|
| **前台执行命令在 30 秒后转入后台** | 触发了自适应前台超时机制，避免阻塞聊天界面。 | 属于正常预期行为。进程在后台安全运行，命令完成时会自动收到推送；Agent 无需轮询等待。 |
| **工具调用报错 `[Circuit Breaker] Quota reached`** | Agent 在单轮内调用单体工具超过 5 轮或工具执行总时长超过 90 秒。 | Agent 被要求拆解子任务并使用 `delegate_task` 委派子代理并行处理，或先回复用户当前进展。 |
| **子代理（Subagent）会被熔断或脱壳限制吗？** | 子代理拥有独立生命周期。 | 不会。以 `sa-` 开头的子任务 ID 或 `platform="subagent"` 自动跳过 30s 脱壳定时器与熔断检测，且享受 0 提示词注入开销。 |
| **中途发送了纠偏消息，Agent 会被卡住吗？** | 包含自愈检测。 | 不会。用户中途发送 Steer / 插话消息后，插件会自动清零单轮计数器并重置执行耗时，重新赋予完整配额。 |
| **如何在本地运行测试？** | 项目包含完备的测试套件。 | 在项目根目录下执行 `pytest -v` 即可运行全部单元测试。 |

---

## 🛠️ 环境兼容性

- **Hermes Agent**: v0.8.0 及以上版本
- **Python**: 3.10, 3.11, 3.12+
- **操作系统**: Linux, macOS, WSL

---

## 📄 License

本项目采用 [MIT License](LICENSE) 开源协议。
