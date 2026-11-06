# Hermes Proactive Agent（`proactive-agent`）

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.1.10-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

`proactive-agent` 是 [Hermes Agent](https://hermes-agent.nousresearch.com) 的插件，让聊天代理像一位带着团队的私人秘书一样工作。

你是老板。**主代理**是秘书：随时能找到，理解你要什么，负责判断和拍板，最后向你汇报。**子代理**是工人：接手耗时长、步骤多、输出杂的活。

## 设计思路

插件跟着一个任务走完全程，每个环节只做一件简单的事。

```
 你 ──► 秘书（主代理）──────── delegate_task ────────► 工人（子代理）
        1. 单轮预算               2. 交接                3. 工人限制
          ▲                                                 │
          └─────────────── 4. 结果与进度 ◄──────────────────┘
```

1. **秘书随时能回话。** 它可以自己调查和思考，但每一轮有时间预算，长命令转到后台跑。
2. **交接单由秘书来写。** 秘书本来就了解整段对话，所以由它自己写交接单。插件只把明显太短的交接单退回一次，并补上工人无从知道的内容：你最近说的话、你的记忆和几条工作规则。
3. **工人不空转。** 工人有步数上限；反复做同一件事、得到同一个结果，就叫停。
4. **事情有收尾。** 每个工人结束后，秘书会知道结果如何、该核实什么；工人运行期间，你能看到进度。

Hermes 已经做了的安全检查，交给 Hermes：子代理执行危险命令默认会被拒绝（`delegation.subagent_auto_approve: false`），文件工具也会拒绝覆盖已经过期的文件。

## 功能

### 1. 单轮预算

| 本轮已用时间（可配置） | 效果 |
|---|---|
| 60 秒以内 | 不干预 |
| 60 秒起 | 在下一个工具结果后附一次提醒：1 到 2 步内要么回复你，要么派活 |
| 120 秒起 | 暂停改文件、执行命令这类工具；只读工具和 `delegate_task` 仍可用 |

- 你在一轮中途发来新消息，预算会清零。
- 前台命令超过 30 秒，会通过 Hermes 自带的机制转到后台：进程继续运行，结束后 Hermes 会通知秘书。秘书会被告知命令没有失败。
- 拒绝 `process_manage(action='wait')`：等待后台任务会让聊天再次卡住。
- 插件在系统提示词里加了一小段，事先把这些规矩告诉秘书。

### 2. 交接

- `delegate_task` 的 `context` 不足 120 个字符时，每轮退回一次，并附一份简短清单：已知什么、具体目标、哪些不能动、什么时候停、什么算成功。第二次提交一定放行。
- 每个放行的任务，在秘书自己写的 context 之后，会附上：
  - 你最近几条消息的原文，让“别动 X”这类约束原样传给工人；
  - 你的 `USER.md` 和 `MEMORY.md`（有长度上限）；
  - 工作规则：只做份内的事、同一个错误出现两次就停、危险改动必须有回退办法、核实真实结果、用你的语言写汇报；
  - 默认的 `output_schema`（`status`、`summary`、`evidence`、`blockers`），任务自带时不覆盖。

不会额外调用模型：交接单由秘书来写，它已经缓存了整段对话。

### 3. 工人限制

只对受管会话派出的子代理生效。

- **同一步、同一个结果。** 重复 3 次时，提醒工人换个办法；重复 5 次就叫停：后续工具调用一律拒绝，要求它以 `blocked` 状态汇报。聊天里会给你发一条简短提醒。
- **步数上限。** 默认 60 轮工具调用，用到 80% 时提醒它收尾。

### 4. 结果与进度

- 工人结束后，秘书在下一轮会收到一条提示：工人给出的证据；或者指出它声称成功却没有证据、没有做完、被插件叫停。
- 每一轮，秘书都能看到还有哪些工人在跑；系统提示词会要求它停掉已经过时的工人（`delegate_task(action='stop')`）。
- 工人运行期间，每 5 分钟往聊天里发一句进度。这需要运行在网关平台上，通过 `ctx.post_message` 发送，语言跟随你。

### 生效范围

只有 `governed_platforms` 里的平台会受影响，默认是 `["weixin"]`。其他平台、定时任务，以及由不受管会话派出的子代理，都不受影响。

## 配置

所有配置项写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下。直接写在 `proactive-agent:` 下面的配置项会被 Hermes 忽略。每个配置项也可以用环境变量 `PROACTIVE_<配置名大写>` 覆盖。

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `governed_platforms` | `["weixin"]` | 受管的平台，对其主代理和它派出的子代理生效 |
| `terminal_auto_detach_seconds` | `30` | 前台命令超过这个秒数就转到后台（0 表示关闭） |
| `turn_soft_seconds` | `60` | 到这个秒数时提醒秘书回复或派活 |
| `turn_hard_seconds` | `120` | 到这个秒数后，本轮暂停改文件和执行命令 |
| `handoff_min_context_chars` | `120` | `context` 少于这个字符数时，每轮退回一次 |
| `handoff_memory_max_chars` | `4000` | 每次交接附带的记忆上限（0 表示不附带） |
| `worker_max_tool_rounds` | `60` | 工人的步数上限（0 表示不限） |
| `worker_repeat_limit` | `5` | 同一步得到同一结果达到这个次数就叫停工人（提前两次先提醒） |
| `progress_interval_seconds` | `300` | 两次进度推送之间的秒数（0 表示关闭进度和提醒） |

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
```

建议把 `delegation.max_iterations` 设得比 `worker_max_tool_rounds` 大。这样插件的步数上限会先触发，让工人先交一份汇报，而不是直接撞上 Hermes 的硬上限。

## 安装

```bash
hermes plugins install github:JerryLiu369/hermes-proactive-agent
hermes plugins enable proactive-agent
```

手动安装：把仓库克隆到 `~/.hermes/plugins/proactive-agent`，然后运行 `hermes plugins enable proactive-agent`。

## 开发

```bash
pip install pytest pyyaml
pytest -q
```

仓库根目录就是插件包，测试以包的方式加载它（见 `tests/conftest.py`）。`tests/test_hermes_e2e.py` 在能导入 `hermes_cli` 时会针对真实的 Hermes 运行，否则自动跳过。

| 模块 | 作用 |
|---|---|
| `__init__.py` | 注册钩子 |
| `config.py` | 读取配置 |
| `context.py` | 判断调用方：受管主代理、工人还是定时任务 |
| `secretary.py` | 单轮预算、长命令转后台 |
| `handoff.py` | `delegate_task` 检查与附带内容 |
| `workers.py` | 工人限制、结果提示、进度推送 |
| `util.py` | 文本和工具结果的辅助函数 |
| `prompt.py` | 系统提示词片段 |

## 兼容性

- 需要 Hermes Agent 支持 `pre_tool_call` 改写参数、`subagent_start` / `subagent_stop` 和 `ctx.post_message`。已在 Hermes v0.22.7 上测试。没有 `post_message` 时不推送进度。
- Python 3.10 及以上。

## License

[MIT](LICENSE)
