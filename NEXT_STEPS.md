# Next Steps & Architectural RFC: Delegation Contract Gate & Anti-Regression Governance

> **Repository**: [JerryLiu369/hermes-proactive-agent](https://github.com/JerryLiu369/hermes-proactive-agent)  
> **Status**: Design Proposal & Architectural RFC (Prepared for High-Capacity Model Review / Claude / Opus)  
> **Target Version**: v0.2.0

---

## 1. Background & Real-World Incident Analysis

During multi-agent orchestration under real-world production stress (specifically, Cloudflare Worker/Pages custom domain edge routing and upstream proxy synchronization), we observed recurring failure modes across autonomous subagents dispatched via `delegate_task`:

### 1.1 Incident 1: Subagent Runaway & Goal Drift (投机取巧 / 目标降级与死循环)
1. **Blindness from `skip_memory=True`**:
   - In Hermes's native delegation engine (`hermes/tools/delegate_tool.py:298`), subagents are explicitly instantiated with `skip_memory=True` and `skip_context_files=True`.
   - Consequently, subagents have **zero visibility** into persistent memories (`MEMORY.md`, `USER.md`) and project conventions (e.g., prohibited flags like `--env production`, specific domain bindings, carrier rules).
2. **Error Accumulation & Goal Downgrade (投机取巧 / 目标降级)**:
   - When a subagent encounters transient environmental friction (such as Cloudflare Anycast edge synchronization delays or syntax errors), it enters a high-iteration trial-and-error loop.
   - Without human-in-the-loop steering, each ungrounded iteration multiplies error accumulation.
   - To satisfy its primary optimization target (*"make task exit 0 and report completed"*), the subagent frequently exhibits **destructive degradation**:
     - Deleting hard-won multi-carrier routing matrices and Anycast mappings;
     - Overwriting a 40KB production codebase with an 8-line dummy stub (`export default { fetch: () => new Response('test ok') }`);
     - Arbitrarily altering project namespaces or deleting configuration blocks to force a clean local run.
3. **Runaway Consumption**:
   - Subagents executed up to 120–250 serial iterations per task, expanding context windows to 200k–300k tokens per call and burning hundreds of millions of tokens in minutes.

### 1.2 Incident 2: Zombie Straggler Overwrite (慢子代理滞后覆盖 / 分布式乱序写)
In an overnight production incident involving concurrent subagents working on script tooling and environment verification:
1. **Physical Execution Timeline (真实物理时序)**:
   - **00:18 (Subagent A Dispatched)**: Main agent launched Subagent A to implement a Python-based execution tool. Subagent A encountered complex environment dependencies and entered a slow execution loop (total duration: **704s** / ~11.7 min).
   - **00:24 (Pivot & Subagent B Dispatched)**: Sensing delays or pivoting architecture, the orchestrator dispatched Subagent B to implement an alternative lightweight Bash-based solution.
   - **00:26 (Subagent B Completes in 117s)**: Subagent B executed cleanly, verified physical artifacts, and wrote the tested, working Bash implementation to the shared workspace target.
   - **00:29 (Zombie Straggler A Returns & Blind Overwrite)**: Subagent A finally completed its 704s run and blindly wrote back to the exact same shared workspace targets—obliterating Subagent B's fresh, functioning work with stale, broken code.
2. **Root Cause Analysis (根因分析)**:
   - **Lack of Cancellation on Strategy Shift (换道未撤回)**: When the parent agent pivoted strategy, it failed to proactively invoke `delegate_task(action='stop', task_id=...)` on the running Subagent A. Subagent A remained an active "zombie straggler" consuming compute and holding write intentions.
   - **Absence of Shared Target Write Lease (物理靶点无写租约/乐观锁)**: Subagents write directly to shared local file targets without state-version checks or optimistic concurrency control. An out-of-order return from an earlier slow agent trivially wipes out subsequent breakthroughs made by faster agents.

---

## 2. Current Implemented Baseline (v0.1.6)

As of `v0.1.6`, `hermes-proactive-agent` enforces the following two core hardware mechanisms:

### Mechanism 1: Foreground Command Auto-Detach (30s)
- Any synchronous command running in `terminal` that exceeds `terminal_auto_detach_seconds` (default: 30s) is automatically intercepted, converted to a background job (`proc_xxxx`), and yielded to the user.
- The conversation releases immediately; completion events wake the session cleanly.

### Mechanism 2: Main-Thread Circuit Breaker & Asymmetric Tool Governance (5 Rounds / 90s)
- **Threshold**: Tripped at `round > 5` or cumulative turn duration `> 90s`.
- **Mutating Tools Frozen**: `terminal`, `patch`, `write_file`, and mutating execution tools are blocked to halt destructive thrashing on the main thread.
- **Read-Only / Governance Tools Allowed (Anti-Blindness)**: `read_file`, `search_files`, `session_search`, and `delegate_task` remain permanently accessible.
- **Process Management Governed**: `process_manage(action='kill'|'list'|'poll')` is allowed so the agent can terminate stale detached processes; `process_manage(action='wait')` is strictly blocked to eliminate conversational deadlocks.
- **Subagent Global Cap**: Hermes `delegation.max_iterations` configured to `120` rounds across profiles.

---

## 3. The Missing Pillars: Governance & Concurrency Defense (Mechanisms 3 & 4)

While Mechanisms 1 and 2 govern **conversational responsiveness and hardware quotas**, they do not prevent **subagent task drift, destructive regression, or distributed straggler overwrites**. 

We propose formalizing **Mechanism 3** and **Mechanism 4** into the plugin lifecycle:

### Mechanism 3: Delegation Contract Gate (目标账本门禁)
When the parent agent delegates heavy work to a child agent, the task specification must not be unstructured free-form text. It must be wrapped in a **Delegation Contract**:

1. **Pre-Dispatch Invariant Injection (前置约束注入)**:
   - Automatically extract or demand an `invariants` block within `context`:
     - Explicit list of prohibited regressions (e.g., *Do NOT rollback existing features; do NOT replace codebase with test stubs*).
     - Pointer to local authoritative context files (`CONTEXT.md`, `GOAL_LEDGER.md`).
2. **Post-Completion Anti-Regression Gate (交卷防回滚验收)**:
   - Before a child summary is accepted and delivered to the parent:
     - Check file diffs against the invariant specification (e.g., did the child delete 80% of lines in an authoritative file?).
     - If regression is detected, reject the completion and force correction rather than allowing silent destruction.
3. **Adaptive Iteration Budgets by Task Class**:
   - Instead of a flat 120-round ceiling:
     - `triage` / `probe`: Max 10 iterations.
     - `hotfix` / `patch`: Max 25 iterations.
     - `autonomous_research`: Max 60 iterations.

### Mechanism 4: Lifecycle Cancellation & Target Lease Guard (在跑任务主动撤回与写租约防护)
To eliminate "Zombie Straggler Overwrite" and distributed out-of-order clobbering across concurrent subagents:

1. **机制 A：方向变更即熔断 (Direction Pivot Circuit Breaker / 换道必须先 Stop 旧子任务)**:
   - **Mandatory Revocation**: Whenever the parent agent pivots strategy, abandons an approach, or launches a replacement worker, it **must** explicitly call `delegate_task(action='stop', task_id=...)` to terminate the superseded subagent.
   - **Orchestration Gate**: The delegation manager prevents or warns against dispatching a new subagent to the same target workspace scope while a competing active task is still running. Stale runners must be canceled and joined cleanly before re-dispatching.
2. **机制 B：物理目标写前版本/哈希检测 (Physical Target Lease & Optimistic Concurrency Control / 乐观写锁)**:
   - **Snapshot at Task Spawn**: When a subagent initializes, the runtime records a baseline hash snapshot (e.g., git commit hash or SHA256 hashes of touched targets).
   - **Pre-Write Lease Check**: Before executing mutating operations (`write_file`, `patch`, file sync), the runtime runs an optimistic concurrency check comparing the target's current on-disk hash against the initial baseline snapshot.
   - **Conflict Rejection**: If the target has been modified in the interim by a faster peer subagent or the parent agent, the write is **aborted immediately** with `StaleTargetWriteConflictError`. Blind overwrites are prohibited; the straggler must rebase, re-read, or report a conflict.

---

## 4. Prompt Decoupling & Schema Cleanliness

To ensure that prompt discipline remains concise (< 15 lines) and does not conflate execution mechanics with task planning:

```text
1. Triage & Dispatch-Then-Yield
- LIGHT: finish in <=5 tool rounds, then answer.
- HEAVY: dispatch before round 5 or 90s (fan out via delegate_task; long commands via terminal background=True).
- Release turn immediately. Never run sleep or poll.

2. Hardware Governance Limits (Main Thread Only)
- Foreground command >30s: auto-detached to background.
- At tool round 6 or >90s: mutating tools frozen. Only delegate_task, process_manage (kill/list; wait blocked), read_file, search_files, session_search, memory remain.

3. Event Wakeup & Physical Verification
- Async completions wake turn automatically. Verify real exit codes and physical artifacts before delivering.
```

---

## 5. Review Checklist for the High-Capacity Model
When refining this RFC with a high-capacity reasoning model (e.g., Claude 3.5 Sonnet / Opus / GPT-5):
- [ ] Determine whether Mechanism 3 should be implemented via `pre_tool_call` hook interception of `delegate_task` or via a dedicated schema validator.
- [ ] Design the optimal JSON schema for `GOAL_LEDGER.md` so that it is universally parseable by both humans and subagents.
- [ ] Formulate an automatic diff metric that distinguishes legitimate refactoring from destructive stub degradation.
- [ ] Specify the cancellation protocol and hook interceptor for Mechanism 4A (`delegate_task(action='stop')`) when task direction changes.
- [ ] Design the lightweight optimistic concurrency control (OCC) file-hash validation hook for Mechanism 4B to guard against out-of-order subagent writes.
