# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

[English](README.md) | 简体中文

[Hermes Agent](https://hermes-agent.nousresearch.com) 的插件，让聊天代理像一位带着团队的秘书一样工作。

在聊天软件里，一个埋头干十分钟活的代理，就是一个十分钟找不到的代理。Hermes 已经支持主代理把活交给在后台运行的子代理。这个插件让这种交接像一位好秘书那样运转：**主代理**随时找得到，负责拍板和汇报；**子代理**去做慢活；每件活结束时，结果都经过核实，也不会被忘掉。

## 安装

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

装好后重启网关；升级用 `hermes plugins update proactive-agent`。另外建议：一个子代理调用模型超过 60 次左右，通常已经卡住了。把 Hermes 的步数上限调低，它会更早交汇报。

```yaml
delegation:
  max_iterations: 60
```

## 为什么需要它

聊天代理自己派活时，会出五种问题。插件逐一应对。

| 没有插件时 | 有插件后 |
|---|---|
| 主代理自己跑构建、长时间排查，聊天一直没有回音 | 每一轮有时间预算，长命令转到后台 |
| 子代理一开始什么都不知道：没看过对话，不知道约束，也不知道试过什么 | 太单薄的交接单会被退回，每份交接单都带上你的原话、记忆和之前的进展 |
| 卡住的子代理反复做同一件事，直到步数耗光 | 同样的尝试做到第 5 次就叫停 |
| "做完了"只是它自己说的 | 主代理会被告知拿回了哪些证据，并被要求核实 |
| 子代理结束或 Hermes 重启后，它做过什么就没人知道了 | 任务账本记下每个任务，并交给下一个子代理 |

## 它做什么

### 1. 聊天不卡住

主代理可以思考、可以调查，但每一轮都有预算。

| 本轮已用时间 | 效果 |
|---|---|
| 60 秒以内 | 不干预 |
| 60 秒起 | 提醒一次：1 到 2 步内回复你，或者派活 |
| 120 秒起 | 暂停改文件和执行命令；读取、`delegate_task` 和收尾用的工具仍可用 |

- 你在一轮中途发来新消息，计时重新开始。
- 收尾用的工具是 `memory`、技能相关工具、`todo_list`、`clarify`，以及只用于查看、轮询、读日志或结束后台任务的 `process_manage`。
- 终端命令跑了 30 秒还没结束，就通过 Hermes 自带的机制转到后台：进程继续跑，结束时 Hermes 会通知，主代理也会被告知命令没有失败。
- 拒绝干等后台任务（`process_manage(action='wait')`），因为那会让聊天再次卡住。

### 2. 子代理带着信息出发

主代理了解整段对话，所以交接单由它来写。以下两种情况，`delegate_task` 每轮退回一次：

- `context` 不足 120 个字符。退回时提示它补上：已知什么、具体目标、哪些不能动、什么时候停、什么算成功；
- 提到了另一个在跑的子代理正在改的文件。主代理可以等它做完、用 steer 改它的方向，或者先停掉它。

第二次提交一定放行。放行的任务会在主代理自己写的 context 之后，附上一份简报：

| 内容 | 作用 |
|---|---|
| 你最近几条消息的原文 | 让"别动 X"这类约束传到子代理手里 |
| `USER.md` 和 `MEMORY.md` | Hermes 不给子代理加载记忆 |
| 正在跑的其他子代理和它们在改的文件 | 别去碰这些文件 |
| 这个对话最近没做完的任务 | 摘要、阻碍、收到的修正、改过的文件和日志路径，接着做而不是从头来 |
| 工作规则 | 只做份内的事、同一个错误出现两次就停、危险改动要能回退、核实真实结果 |
| 默认的 `output_schema` | `status`、`summary`、`evidence`、`blockers`；任务自带时不覆盖 |

### 3. 子代理不空转

子代理用同一个工具、同样的参数，得到同样的结果：第 3 次提醒它换个办法，第 5 次叫停。次数按整个运行累计，不要求连续。比较的是结果的第一行，长数字和哈希会被遮掉，所以只是请求 ID 变了仍算同样的结果。叫停后它的工具调用一律拒绝，必须以 `blocked` 汇报。

Hermes 自带的循环检测也能发现重复，但对子代理只提醒、不叫停。总步数上限仍由 Hermes 管（`delegation.max_iterations`）。

### 4. 结果有核实，事情有记录

**每个结果都要过目。** 子代理结束后，主代理下一轮开头会收到一条提示：子代理给出的证据；或者提醒它声称成功却没有证据、没做完、因为原地打转被叫停。被叫停时，主代理会用一句话告诉你哪里卡住了、接下来怎么办。主代理自己停掉的任务会注明是主动停的，不算失败。

**每个任务都有记录。** 任务账本给每个任务记一条：目标、状态、改过的文件、通过 `action='steer'` 发过的修正、实时日志路径，以及结束时的摘要、证据和阻碍。

**工作能接续下去。** Hermes 无法把结束的子代理再唤起来，所以同一个对话里的下一个子代理会拿到没做完那个任务的记录。Hermes 中途重启时，主代理会被告知哪些任务丢了、副作用不确定、动过哪些文件。提示存在账本里，等到那个对话的下一轮才取出，重启也不会丢。

账本存放在 `<hermes home>/plugin-data/proactive-agent/ledger.db`，每个对话保留最近 20 个已结束的任务，最长 7 天。

## 设计原则

- **只跟主代理说话。** 插件只往主代理的上下文里加提示，从不直接往你的聊天里发消息。
- **不调用模型。** 所有检查都是普通代码。
- **Hermes 的事交给 Hermes。** 命令审批、过期文件保护、步数上限、循环提醒、完成结果的投递，都由 Hermes 自己负责。插件只补 Hermes 没管到的地方。
- **绝不碍事。** 插件出错只记日志，不会拦住任何工具。
- **只管你指定的地方。** 只有 `governed_platforms` 里的平台（默认 `["weixin"]`）及其派出的子代理受管，其他平台和定时任务都不受影响。

## 配置

配置写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下。每一项都可以用环境变量 `PROACTIVE_<配置名大写>` 覆盖。

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
```

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `governed_platforms` | `["weixin"]` | 受管的平台，对主代理和它派出的子代理生效 |
| `turn_soft_seconds` | `60` | 到这个秒数时，提醒主代理回复或派活 |
| `turn_hard_seconds` | `120` | 到这个秒数后，本轮暂停改文件和执行命令 |
| `terminal_auto_detach_seconds` | `30` | 终端命令跑超过这个秒数就转到后台（`0` 表示关闭） |
| `handoff_min_context_chars` | `120` | `context` 少于这个字符数时，每轮退回一次 |
| `worker_repeat_limit` | `5` | 同样的尝试做到多少次就叫停子代理（提前两次先提醒） |

## 兼容性

- Hermes Agent 0.22 及以上（已在 v0.22.7 上测试）。
- Python 3.10 及以上。

## 开发

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

能导入 `hermes_cli` 时，`tests/test_hermes_e2e.py` 会通过真实安装的 Hermes 运行插件，否则自动跳过。

```
proactive-agent/      插件本体，也是 Hermes 唯一安装的目录
├── secretary.py      1. 单轮预算、长命令转后台
├── handoff.py        2. 交接检查与简报
├── workers.py        3. 子代理登记、重复叫停
├── reports.py        4. 结果提示、重启恢复
├── ledger.py         4. 任务账本
├── prompt.py         给主代理的系统提示词片段
├── context.py        判断调用方：主代理、子代理还是定时任务
├── config.py         配置
├── util.py           辅助函数
├── __init__.py       注册钩子
└── plugin.yaml       插件清单
tests/                每个模块一个测试文件，外加针对 Hermes 的端到端测试
```

## 许可证

[MIT](LICENSE)
