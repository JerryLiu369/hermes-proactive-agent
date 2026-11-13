# Hermes Super Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-super-agent?label=release)](https://github.com/JerryLiu369/hermes-super-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-super-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-super-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

[English](README.md) | 简体中文

Hermes Super Agent 是专为 [Hermes Agent](https://hermes-agent.nousresearch.com) 设计的 **Lead–Worker Super Agent** 架构协作插件。

在传统的单代理聊天架构中，主会话往往在执行繁重任务时陷入漫长沉默，或者派发出无上下文、容易死循环且随意遗漏产物的子代理。本插件确立了清晰的职责解耦机制：

- **Lead Agent（主会话主控代理）：** 持续驻留聊天交互通道，理解用户意图，制定执行计划，勘察前置事实，决策或上报关键分歧，并在向用户汇报前核实交付物实证。
- **Worker Subagents（后台工人子代理）：** 在后台异步执行耗时冗长、工具输出庞大或需要多步试错的工作（代码构建、测试套件跑测、开放式网络调研、批量重构）。Worker 获得包含会话上下文、用户与环境记忆、并行任务状态及操作边界的完整交接简报，受到工具死循环熔断与越权盲猜拦截的严格约束，且所有执行轨迹统一持久化于 SQLite 任务台账。

本插件基于 Hermes 原生钩子与中间件以确定性纯 Python 逻辑运行，自身不发起任何模型推理调用，不消耗额外 Token，且绝不越过主控代理向用户通道私自发送消息。用户与代理之间的交互始终由 Lead Agent 统一主导。

---

## 安装与快速启动

通过 Hermes CLI 安装并启用插件：

```bash
hermes plugins install JerryLiu369/hermes-super-agent/super-agent
hermes plugins enable super-agent
```

在 `~/.hermes/config.yaml` 中配置推荐宿主参数（推荐 `delegation.max_iterations: 90` 约束子代理步数上限，修改后需重启 Hermes 网关）：

```yaml
delegation:
  max_iterations: 90                # 建议子代理最大迭代轮数
plugins:
  entries:
    super-agent:
      allow_gateway_injection: true # 允许在后台汇报被用户打断时唤醒 Lead Agent 补发
```

配置 `allow_gateway_injection: true` 后，当后台 Worker 完成汇报时若恰好被用户并发发送的新消息冲淡，插件可通过 Hermes 内部网关消息注入队列唤醒 Lead Agent 补全交付汇报。若不配置该项，未汇报结果将在用户下一次发言时顺带附带。

---

## 从 `hermes-proactive-agent` (0.1.x) 升级

Hermes Super Agent v0.2.0 是 `hermes-proactive-agent` 的直接架构演进版。升级现有环境的步骤如下：

1. **安装并启用新插件：**
   ```bash
   hermes plugins install JerryLiu369/hermes-super-agent/super-agent
   hermes plugins enable super-agent
   # 可选：停用旧版本
   hermes plugins disable proactive-agent
   ```
2. **迁移 `~/.hermes/config.yaml` 配置项：**
   将配置键从 `plugins.entries.proactive-agent` 重命名为 `plugins.entries.super-agent`：
   ```yaml
   plugins:
     entries:
       super-agent:
         allow_gateway_injection: true
         settings:
           governed_platforms: ["weixin"]
   ```
3. **可选迁移历史任务台账：**
   如需继承 v0.1.x 的历史任务记录：
   ```bash
   mkdir -p ~/.hermes/plugin-data/super-agent
   cp ~/.hermes/plugin-data/proactive-agent/ledger.db ~/.hermes/plugin-data/super-agent/ledger.db
   ```
4. **环境变量兼容说明：**
   插件优先读取 `SUPER_AGENT_<KEY>` 环境变量，同时保留对 `PROACTIVE_<KEY>` 的全量向下兼容回退支持。

---

## 解决的核心问题

在即时通信通道（如微信）中使用原生代理委托协作时，普遍存在以下六类问题：

1. **主通道长时间失声卡死：** 主代理在主会话中直接执行大型构建、脚本排障或多步文件写入，导致会话无响应长达数十分钟。
2. **子代理缺乏上下文与记忆：** 原生派发的子代理是无状态全新会话，既看不到近期聊天记录，也未加载 `USER.md` 与 `MEMORY.md`，极易违反用户预设的操作禁区。
3. **工具报错无限死循环与擅自臆测：** 子代理反复执行相同错误命令直至步数耗尽；遇到架构分支或权限拦截时，不向主代理上报，而是擅自做出非授权假设。
4. **交付物未经核查直接采信：** 子代理返回口头“已完成”而无具体实证，主代理不加校验直接转述给用户。
5. **用户插话导致后台产物吞没：** 后台子代理完成交付时，若用户恰好发送了新问题，主代理往往仅回答新问题，导致刚刚出炉的任务产物被彻底遗忘。
6. **网关重启丢失任务状态：** 原生子代理任务仅驻留内存，网关意外退出或重启后，正在修改的文件、中间指令和未汇报产物全部丢失。

---

## 核心机制

### 1. Lead Agent 响应性保障

Lead Agent 所在的主会话受到确定性时间预算治理：

- **60 秒软预算提醒 (`turn_soft_seconds: 60`)：** 主代理收到阶段性提醒，指导其在 1 到 2 步内输出已有结论或调用 `delegate_task` 将重任务转交后台。
- **120 秒硬预算暂停 (`turn_hard_seconds: 120`)：** 暂停文件编辑、文件写入与终端执行等修改类工具调用。只读与总控类工具保持开放：文件读取、图片分析 (`vision_analyze`)、搜索、`delegate_task`、`memory`、`todo_list`、`clarify`、技能工具与进程查询 (`process_manage` 的 `list`、`poll`、`log`、`kill`)。
- **中途插话重置计时：** 当用户在回合中途发送新指令时，本轮计时器自动重置，保障代理有完整时间理解新需求。
- **压缩过程暂停计时：** Hermes 触发会话上下文自动压缩时不计入回合耗时。
- **30 秒终端自动转入后台 (`terminal_auto_detach_seconds: 30`)：** 前台终端命令执行超过 30 秒时，通过 Hermes 原生 yield 机制自动转为后台进程执行，Hermes 记录 PID 并在结束时通知主代理。禁止主代理调用 `process_manage(action='wait')` 阻塞主通道。
- **系统提示词注入：** 针对受管会话在 `after_memory` 位置注册 `super-agent.discipline` 提示词段落，约束 Lead Agent 的调度原则。

### 2. Worker Subagent 门禁与交接简报

子代理默认不继承主会话上下文。插件通过门禁校验与结构化上下文注入保证交付质量：

- **委托门禁检查：** 校验 `delegate_task` 调用。若任务描述不足 120 字符 (`handoff_min_context_chars: 120`)，插件在同回合内返回一次提示，要求补充已知事实、目标路径、不可变更约束、终止条件与验证方式；若同回合再次提交则予以放行。
- **文件写入冲突防护：** 若新派发任务试图修改另一个活跃子代理正在编辑的文件，该调用在同回合内被拦截一次，防止并发写冲突。
- **全量上下文交接装配：** 通过门禁的任务在启动时自动装配包含以下信息的交接包：
  - 最近 6 条用户原话，保留即时上下文与否定性约束。
  - 全量 `USER.md` 与 `MEMORY.md` 内容（原生子代理不加载此类记忆）。
  - 当前会话中正在并发运行的 Worker 列表及其目标文件。
  - 本会话近期未完成任务档案（已知事实、阻碍点、待决问题、修改文件、日志路径）。
  - 确定性执行纪律：严格限定工作范围、同一错误重试两次即止、遇阻升级而非臆测、高危操作需有回滚方案、交付必须包含 checkable 实证。
  - 标准化 JSON 汇报格式规范。

### 3. 循环熔断与决策升级

- **输出归一化比对：** 插件持续监控子代理全生命周期的工具调用结果，屏蔽输出中的可变 ID、时间戳与内存地址后进行哈希归一化比对。
- **多步循环熔断 (`worker_repeat_limit: 5`)：**
  - **第 3 次相同输出：** 向子代理注入明确警告，要求调整策略或记录阻碍。
  - **第 5 次相同输出：** 强制终止子代理。后续工具调用均被拒绝，强制要求以 `blocked` 状态收尾。整体步数仍受 Hermes `delegation.max_iterations` 约束（建议设为 90）。
- **决策升级机制 (`needs_decision`)：** 子代理无权直接与用户沟通（Hermes 不向子代理开放 `clarify` 工具）。当遇到多选架构权衡、生产变更、不可逆操作或产生费用的分支，或者遭遇 Hermes 命令审批拦截与安全规则拦截时：
  - 子代理立即停止盲猜，以 `needs_decision` 状态返回明确的 `question`（待决问题）与 `options`（备选方案）。
  - 若属于审批拒绝，插件即刻注入指令禁止子代理绕道规避。
  - Lead Agent 接收到此上报。若已有上下文或可查事实足以决断，由 Lead Agent 直接定夺；若属于用户意图或偏好，Lead Agent 组织简练提示向用户发起确认，并在获得答复后派发新 Worker 承接任务。

### 4. 交付实证核查、防漏报保障与任务台账

- **交付物实证核验：** 当子代理报告结束时，Lead Agent 下一回合开头会注入实证核验提示：
  - 若状态为 `completed`，列出其提供的实证线索，要求 Lead Agent 抽检确认后再向用户反馈。
  - 若缺少实证或因循环被系统拦截，注入警告提示，防止虚假交付。
- **防漏报状态机机制：** 当后台子代理完成交付与用户发送新消息发生竞态时：
  - 该任务结果被标为 `unreported`（待汇报）。
  - 若 Lead Agent 在混合回合中调用了工具，工具返回值末尾附带提示，督促其在当前回复中一并汇报该任务结果。
  - 若 Lead Agent 未调用工具直接回复，插件通过 `ctx.inject_message` 触发单次唤醒（每个任务最多尝试 2 次），让代理补发汇报或输出 `[SILENT]`。
  - 若未开启注入或唤醒被中断，未汇报结果将在用户下一条消息到达时附带。超过 24 小时的旧结果自动转为归档。
- **持久化 SQLite 任务台账：** 所有任务状态记录保存在 `<hermes home>/plugin-data/super-agent/ledger.db` 中。记录字段包含任务 ID、父会话、子会话、目标、运行状态（`running`、`completed`、`partial`、`blocked`、`needs_decision`、`failed`、`lost`、`stopped`）、修改文件、引导指令、完整轨迹路径与汇报状态。
- **网关崩溃恢复：** 网关重启时，先前的活跃子代理被自动标记为 `lost`。Lead Agent 在下一次交互时收到中断报告与曾被修改的文件列表，避免基于过时假定继续执行。台账按每个父会话保留最近 20 条已完成记录，最长保留 7 天。

---

## 设计原则

- **主控统一代理：** 插件仅通过注入上下文提示和网关内部唤醒与 Lead Agent 交互，绝不直接向用户通信通道发送消息。
- **事件驱动与确定性：** 无常驻后台轮询线程或定时心跳循环。所有控制逻辑均由原生事件（回合开始、工具执行前后、子代理启停）即时触发。
- **零模型调用开销：** 规则校验、死循环检测、门禁过滤与交接包拼装完全由 Python 原生代码计算完成，不产生额外 Token 费用。
- **宿主职责解耦：** 命令审批、沙箱隔离、最大迭代轮数与工具执行底层完全交由 Hermes 原生体系负责，插件专注于协作编排治理。
- **基于公共标准接口：** 完全通过 Hermes 官方公开钩子 (`pre_llm_call`, `pre_api_request`, `pre_auxiliary_call`, `post_auxiliary_call`, `pre_tool_call`, `post_tool_call`, `transform_tool_result`, `subagent_start`, `subagent_stop`, `on_session_end`, `on_session_reset`, `on_session_finalize`) 与 `tool_execution` 中间件实现。
- **故障隔离与放行保障：** 所有钩子逻辑均包含全量异常保护，若内部逻辑发生未预期错误，记录日志后放行工具执行，绝不中断宿主功能。
- **白名单精准受管：** 仅针对 `governed_platforms`（默认：`["weixin"]`）及其派生的子代理进行治理。CLI 会话、定时任务及未配置平台保持完全原生行为。

---

## 配置参考

配置位于 `~/.hermes/config.yaml` 的 `plugins.entries.super-agent.settings` 下：

```yaml
plugins:
  entries:
    super-agent:
      allow_gateway_injection: true   # Hermes 宿主权限
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| 参数项 | 类型 | 默认值 | 作用说明 |
|---|---|---|---|
| `governed_platforms` | 列表 | `["weixin"]` | 纳入 Lead Agent 与 Worker Subagent 协作治理的通道平台列表 |
| `turn_soft_seconds` | 数值 | `60.0` | 单回合软上限秒数，达到后提醒 Lead Agent 及时回复或派发子代理 |
| `turn_hard_seconds` | 数值 | `120.0` | 单回合硬上限秒数，达到后暂停文件修改与终端执行等动作 |
| `terminal_auto_detach_seconds` | 整数 | `30` | 前台终端命令自动转入后台的超时阈值（`0` 表示不转入后台） |
| `handoff_min_context_chars` | 整数 | `120` | `delegate_task` 的 `context` 参数初次门禁校验的最低字符数 |
| `worker_repeat_limit` | 整数 | `5` | 判定子代理陷入重复循环并强制终止的阈值（第 3 次触发警告） |

### 环境变量覆盖

所有配置项均支持通过环境变量动态覆盖。优先匹配 `SUPER_AGENT_<KEY>`，其次回退匹配 `PROACTIVE_<KEY>`，再匹配 `config.yaml`，最后采用代码默认值：

```bash
export SUPER_AGENT_TURN_SOFT_SECONDS=45
export SUPER_AGENT_WORKER_REPEAT_LIMIT=4
```

---

## 常见问题

**插件会增加模型 Token 消耗吗？**  
不会。插件自身不包含任何 LLM 提示词与模型调用。其注入的内容仅为极短的上下文提示段落，以及给 `delegate_task` 附带的背景简报。触发唤醒补发汇报的回合仅消耗单次简短回复的标准 Token。

**CLI 终端会话、定时任务或其他通道会受影响吗？**  
不会。未声明在 `governed_platforms` 中的平台不受任何干预。CLI 会话和 Cron 任务保持完全原生执行。

**会与 Hermes 原生命令审批或循环检测冲突吗？**  
不会。Hermes 原生的审批流、权限控制与步数上限正常运转。当审批拒绝子代理命令时，插件会拦截其绕道盲猜行为，并将其结构化为 `needs_decision` 上报给 Lead Agent。

**如何避免聊天界面长任务卡死？**  
在 60 秒时提醒 Lead Agent，在 120 秒时暂停耗时写操作；前台终端命令运行超过 30 秒自动转入后台，同时拒绝主会话通过 `process_manage(action='wait')` 进行阻塞等待。

**原生子代理为什么缺乏上下文，插件如何补齐？**  
Hermes 默认以无状态环境派发子代理。插件通过拦截 `delegate_task`，要求任务描述具备必要参数，并自动附带近 6 条用户原话、完整 `USER.md` 与 `MEMORY.md`、并行任务文件及标准操作规范。

**子代理遇到决策分歧或权限拒绝时如何处理？**  
子代理无权调用 `clarify` 与用户对话。遇到多方案抉择或操作拒绝时，子代理以 `needs_decision` 状态返回待决问题与选项。Lead Agent 依据已知事实仲裁或向用户发起一次简要确认，再启动新 Worker 执行。

**用户插话导致后台任务汇报被忽略怎么办？**  
Hermes 会将新消息并入汇报回合，常导致代理遗漏已完成的后台任务。插件将此类任务标记为 `unreported`，通过后续工具执行追加提醒，或在空闲时借助网关注入唤醒 Lead Agent 完成交付。

---

## 仓库结构

```text
super-agent/          实际安装的插件目录（Hermes 加载路径）
├── secretary.py      回合时间预算管理、30 秒终端自动后台化、用户插话处理
├── handoff.py        门禁校验、上下文交接简报装配、操作规范提示
├── workers.py        子代理运行注册表、重复调用监控与熔断、拒绝指令拦截
├── reports.py        交付实证核查、待决问题上报、未汇报结果状态机、崩溃恢复
├── ledger.py         SQLite 任务台账与持久化通知队列
├── prompt.py         Lead Agent 系统提示词段落 (super-agent.discipline)
├── context.py        调用方角色判断（受管 Lead Agent、Worker 子代理、Cron）
├── config.py         配置项解析与环境变量覆盖
├── util.py           文本处理、JSON 序列化与函数签名工具
├── __init__.py       钩子与中间件注册入口
└── plugin.yaml       插件清单与配置架构定义
tests/                单元测试与端到端测试套件
```

---

## 兼容性与本地开发

- **Hermes Agent：** 版本 >= 0.22（已在 v0.22.7 完整验证）。
- **进程隔离支持：** 终端自动后台化特性要求使用默认的 `plugins.isolation: in_process`。在 `host` 模式下命令保持前台执行。
- **Python：** 3.10 及以上版本，无需任何外部第三方运行时依赖。
- **自动化测试：** CI 工作流针对 Python 3.10 至 3.14 进行 `ruff check` 与 `pytest` 全量测试。

本地执行测试套件：

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

---

## 面向 AI 代理的技术索引

针对 AI 编码代理与自动化工具的参考文件：
- [llms.txt](llms.txt)：高信息密度的架构规范与技术摘要。

---

## 许可证

[MIT](LICENSE)
