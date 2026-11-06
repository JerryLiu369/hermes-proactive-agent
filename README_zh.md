# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

[English](README.md) | 简体中文

这是 [Hermes Agent](https://hermes-agent.nousresearch.com) 的一个插件。它让你在聊天里对话的那个 Hermes 像一位靠谱的秘书那样做事：慢活派给手下去做，自己随时找得到；每件活做完，结果要核实过、确实告诉了你，也要记下来。

下文把和你对话的这个 Hermes 叫作"助手"，把它派出去干活的叫作"子代理"。

插件是为在聊天软件里使用 Hermes 准备的。它自己不调用任何模型，也从不往你的聊天里发消息，你看到的每句话都出自助手。

## 安装

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

建议再在 `~/.hermes/config.yaml` 里加两项设置（都可以不加），然后重启网关：

```yaml
delegation:
  max_iterations: 60                # 需要更多步数的子代理，多半已经卡住了
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true # 允许插件叫醒助手，把被你打断的汇报补上
```

不开 `allow_gateway_injection` 也没关系，被你打断的汇报会等你发下一条消息时再补。如果插件是用别的方式装的，把 `proactive-agent` 换成 `hermes plugins list` 里显示的名字。

## 为什么要做这个

Hermes 本来就能把活交给子代理在后台去做。可一旦在聊天里用起来，总有几件事会出岔子：

- 助手自己跑起了构建，或者一头扎进排查，聊天里十分钟没有动静。
- 子代理出发时两眼一抹黑：你说过什么、之前试过什么、哪些东西不能碰，它都不知道。
- 卡住的子代理一遍遍跑同一条命令，直到步数耗光。或者遇到一个本不该它来定的选择，干脆自己猜一个。
- 所谓"做完了"，只是子代理自己说的。
- 结果刚回来，你恰好发了条消息。助手回了你这句，那个结果就再没人提起。
- 子代理一结束，或者 Hermes 一重启，它做过的事就无从查起。

这个插件逐一解决这些问题，下面按顺序说。

## 它做了什么

### 聊天不会卡住

助手的每一轮都有时间限制。满 60 秒时，它会收到一次提醒：一两步之内回复你，或者把剩下的活交给子代理。满 120 秒后，这一轮不能再改文件、跑命令，但还能查看、派活和收尾：`memory`、技能类工具、`todo_list`、`clarify`，以及用 `process_manage` 查看、轮询、读日志或结束后台任务。你在一轮中途发来消息，计时就从头开始。

耗时的命令也不会拖住这一轮。终端命令跑了 30 秒还没结束，就借助 Hermes 自带的机制转到后台：进程照常运行，结束时 Hermes 会告诉助手，助手也会知道这条命令并没有失败。用 `process_manage(action='wait')` 干等后台任务会被拒绝，因为那样聊天又会卡住。

### 子代理出发前心里有数

子代理看不到聊天记录，也没有记忆，所以交代任务的话由助手来写。如果这份交代（`context`）不到 120 个字符，或者提到了另一个子代理正在改的文件，每轮会被退回一次，并说明缺了什么。第二次提交一律放行。

放行的任务，会在助手自己写的交代后面再附上一份背景材料：

- 你最近 6 条消息的原话，"别动 X"这类叮嘱能一字不差地传过去；
- `USER.md` 和 `MEMORY.md`，因为 Hermes 不给子代理加载记忆；
- 还有哪些子代理在运行，它们在改哪些文件；
- 这个对话里最近没做完的任务：查到了什么、卡在哪里、收到过哪些纠正、改过哪些文件、日志在哪；
- 几条做事规矩：只做分内的事；同样的错连犯两次就停；不瞎猜，也不绕开拦截；有风险的改动要留退路；说做完之前先确认真实结果；
- 一份默认的回报格式（`output_schema`）：`status`（`completed`、`partial`、`blocked`、`needs_decision` 或 `failed`）、`summary`、`evidence`、`blockers`，需要拍板时再加 `question` 和 `options`。任务自带格式的，以任务为准。

### 子代理不空转、不瞎猜

Hermes 能看出子代理在重复，但对子代理只提醒、不制止。插件会替你数着：子代理用同一个工具、同样的参数，得到同样的结果，第 3 次提醒它，第 5 次叫停。次数按整个运行累计，不要求连续；比较的是结果的第一行，长数字和哈希会先抹掉，所以只是请求 ID 变了，照样算同一个结果。叫停之后，它再调用工具一律被拒，只能以 `blocked` 回报。总步数上限仍归 Hermes 管（`delegation.max_iterations`）。

子代理也没法问你任何事：Hermes 没给它 `clarify` 工具，结束之后也不能再叫回来。所以当它遇到本不该它来定的选择（两种都行的方案、动生产环境、花钱、删数据），交代里又没写怎么办时，就会停下来，以 `needs_decision` 回报，附上一个问题和几个选项。Hermes 会按回报格式检查，缺了问题就让它补一次。

某一步被 Hermes 的审批或你配置的 hook 拦下，或者卡在一个这次运行里没人能批的审批上，也是一样。子代理会当场收到提示（被 hook 拦下时，提示出现在它的下一个工具结果里）：别重试，也别换条路达到同样的效果，把问题带回来。写着"用户拒绝"的拦截也可能是自动的：在某些配置下，Hermes 不问任何人就会拒绝子代理的危险命令。

助手看得到整段对话，很多时候早就知道答案，接着做就是了。真要你来定的事（个人偏好、风险、花钱、被拦下的命令），它会发一条简短的消息问你，附上几个选项。无论哪种，它都会带着答案和上一个任务的记录，再派一个子代理接着做。如果你已经看到了这个问题，到你下一条消息时助手还没跟进，这条消息会提醒它一次。

### 结果有核实、有交代、有记录

子代理做完后，助手下一轮一开始就会收到一条说明：子代理拿出的证据；或者提醒它说成功了却没给证据、没做完、因为原地打转被叫停。如果是被叫停，助手会用一句话告诉你卡在哪、接下来怎么办。助手自己喊停的任务会注明是主动停的，不算失败。

在后台做完的结果，会单独开一轮交给助手。要是这一轮还没结束你就发了消息，Hermes 会把你的消息并进这一轮，助手往往只顾着回你。所以插件会一直把这个结果记作"还没告诉你"，直到展示过它的那一轮，以一次没被你打断的回复收尾：

- 你发消息之后，助手如果又调用了工具，工具结果里会提醒它：回你的话，并在同一条回复里把结果讲了；
- 如果它没调工具就直接回了你，插件会再叫醒它一次，补一条简短的汇报；已经讲过的话就不再出声。叫醒走的是 Hermes 的消息注入，会排在正在进行的那一轮后面，不会插队；
- 如果那一轮被 Hermes 直接中断了，或者不允许叫醒，就由你的下一条消息把提醒带上。

中途你每插进一条消息，都会重新提醒一次，连着发好几条也不会把结果弄丢。每个结果最多叫醒 2 次，超过一天的不再追。

每个任务都会记进一个 SQLite 账本：目标和状态、改过的文件、用 `action='steer'` 发过的纠正、实时日志的位置、结束时的情况（摘要、证据、阻碍和它提的问题），以及你有没有听到结果。Hermes 没法把结束的子代理再叫回来，所以同一个对话里的下一个子代理会拿到没做完的那个任务的记录。Hermes 要是在任务中途重启，助手会知道哪些任务丢了、动过哪些文件，以及它们留下了什么后果还说不准。还没送出的提示和还没告诉你的结果都存在账本里，重启也不会丢。

账本在 `<hermes home>/plugin-data/proactive-agent/ledger.db`，每个对话保留最近 20 个已结束的任务，最多存 7 天。

## 原则

- **只跟助手说话。** 插件只在助手的上下文里加提示；要补上被你打断的汇报，就用一条内部消息叫醒助手。它从不往你的聊天里发消息，跟你说什么由助手决定。
- **不搞定时播报。** 没有进度消息。每条提示都由具体的事情引起（一轮开始、工具返回、子代理结束、一轮结束时还有结果没告诉你），而且只送一次。
- **不调用模型。** 所有判断都是普通代码。
- **Hermes 的事归 Hermes。** 命令审批、过期文件保护、步数上限、循环提醒、把做完的结果交给助手，仍由 Hermes 自己负责。插件只补它没管到的地方。
- **绝不添乱。** 插件自己出错时只记日志，工具调用照常进行。
- **只在你指定的地方生效。** 只有 `governed_platforms` 里列出的平台，以及从那里派出的子代理，才归插件管。其他平台和定时任务一概不碰。

## 配置

配置写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下：

```yaml
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true   # Hermes 的权限设置，见"安装"
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| 配置项 | 默认值 | 作用 |
|---|---|---|
| `governed_platforms` | `["weixin"]` | 插件生效的平台，包括从这些平台派出的子代理 |
| `turn_soft_seconds` | `60` | 一轮满这么多秒时，提醒助手回复或派活 |
| `turn_hard_seconds` | `120` | 一轮满这么多秒后，暂停改文件和跑命令 |
| `terminal_auto_detach_seconds` | `30` | 终端命令跑超过这么多秒就转到后台（填 `0` 关闭） |
| `handoff_min_context_chars` | `120` | `context` 比这短的任务，每轮退回一次 |
| `worker_repeat_limit` | `5` | 同样的尝试到第几次叫停子代理（提前两次先提醒） |

每一项也可以用环境变量 `PROACTIVE_<配置名大写>` 设置，环境变量优先于配置文件。填了无效的值只会记一条日志，然后忽略。

## 常见问题

**会多花 token 吗？**
插件自己不调用模型，只在助手的上下文里加几段文字。为了补汇报而叫醒助手，相当于助手多跑一轮，花费和你多发一条消息差不多。

**会影响定时任务、命令行或其他平台吗？**
不会。只有 `governed_platforms` 里的平台受影响，定时任务和其他平台一切照旧。

**它会取代 Hermes 的审批或循环检测吗？**
不会，它们照常工作。Hermes 的审批拦下子代理的命令时，插件只是告诉子代理别绕开，把问题带回来。

## 兼容性

- Hermes Agent 0.22 及以上（在 v0.22.7 上测过）。
- Python 3.10 及以上，没有运行时依赖。
- CI 在 Python 3.10 到 3.14 上运行 `ruff check` 和 `pytest`。

## 开发

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

能导入 `hermes_cli` 时，`tests/test_hermes_e2e.py` 会通过真实安装的 Hermes 来运行插件，否则自动跳过。

```
proactive-agent/      插件本体，也是 Hermes 唯一会安装的目录
├── secretary.py      每轮的时间限制、长命令转后台、你在一轮中途发来的消息
├── handoff.py        任务交代的检查、背景材料、做事规矩和回报格式
├── workers.py        运行中的子代理、重复叫停、被拦下时的提示
├── reports.py        结果说明、待回答的问题、还没告诉你的结果、重启后的恢复
├── ledger.py         任务账本
├── prompt.py         助手系统提示词里的一段
├── context.py        判断是谁在调用：助手、子代理还是定时任务
├── config.py         配置
├── util.py           辅助函数
├── __init__.py       注册钩子
└── plugin.yaml       插件清单
tests/                每个模块一个测试文件，外加对真实 Hermes 的端到端测试
```

## 给 AI 读的文档

[llms.txt](llms.txt) 是给 AI 代理和答案引擎看的简短索引，[llms-full.txt](llms-full.txt) 把整个项目写进了一个文件。

## 许可证

[MIT](LICENSE)
