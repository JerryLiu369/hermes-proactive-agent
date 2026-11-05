# Next Steps & Design Notes

> **Repository**: [JerryLiu369/hermes-proactive-agent](https://github.com/JerryLiu369/hermes-proactive-agent)
> **Status**: v0.2.0 implements the core of this RFC. Field data: [issue #1](https://github.com/JerryLiu369/hermes-proactive-agent/issues/1).

---

## 1. What went wrong in 0.1.x

Two incident classes were recorded before 0.2 (details in issue #1):

1. **Subagent runaway and goal drift.** Hermes builds subagents with `skip_memory=True` and `skip_context_files=True`
   (`tools/delegate_tool.py`), so a subagent sees only the `goal` / `context` it is given. With thin briefs and a
   120–250 round budget, subagents looped on the same failing command, scanned ports, replaced working code with stubs
   to get a clean run, and reported success without checking the real end state.
2. **Zombie straggler overwrite.** After the main agent changed approach, a slower subagent that was never stopped
   finished late and overwrote the working result of a faster one.

The root cause behind both: governance pointed the wrong way. The main agent was cut off after 5 rounds / 90 s and
forced to delegate before it understood the problem (often with `context: null`), while subagents were fully exempt.

## 2. What 0.2 does (implemented)

| RFC item | 0.2 implementation |
|---|---|
| Free the main agent | Round-count breaker removed. Soft/hard turn budget by time and tool-output volume (60 s / 120 s by default), with a reminder before anything is frozen. |
| Delegation contract gate | `pre_tool_call` on `delegate_task`: thin or risky-without-rollback briefs are returned once per turn; accepted briefs get a chat summary, user memory, working rules and a default `output_schema` via `action: "modify"`. |
| Adaptive iteration budgets | One plugin-side step budget per worker (`worker_max_tool_rounds`), plus fail-fast on repeated identical steps or failures. Hermes ignores a caller-supplied `max_iterations`, so the budget is enforced in the worker's own hooks. |
| Anti-regression gate | Not a diff metric. Instead: working rules forbid stubbing/deleting working code, and results are checked for evidence (`subagent_stop`) before the main agent reports them. |
| Cancel on pivot | A new task whose paths/hosts/domains overlap a running worker is returned once with the `delegate_task(action='stop', ...)` call to make. |
| Target write lease (OCC) | A worker's `write_file` / `patch` is refused when the file changed after the worker started and the worker has not re-read it. |
| Progress visibility | Progress lines and stuck alerts posted to the chat with `ctx.post_message`. |

## 3. Open items

- **Terminal writes are not guarded.** `cat > file`, `sed -i`, `cp` from a worker bypass the write guard; only
  `write_file` and `patch` are checked.
- **Pivot detection is lexical.** It compares paths, IPs and domains named in the briefs; two tasks on the same
  service described differently are not linked.
- **Per-task budgets.** A single step budget applies to every worker. Letting the brief pick a class
  (probe / fix / research) is a possible follow-up.
- **Summary cost.** Each handoff makes one auxiliary model call. `ctx.side_question` (warm cache on the main
  session) would be cheaper but is only available at the end of a turn.
