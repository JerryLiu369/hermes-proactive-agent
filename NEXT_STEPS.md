# Design Notes & Next Steps

> **Repository**: [JerryLiu369/hermes-proactive-agent](https://github.com/JerryLiu369/hermes-proactive-agent)
> **Field reports**: [issue #1](https://github.com/JerryLiu369/hermes-proactive-agent/issues/1)

---

## 1. The problem

Hermes builds subagents with `skip_memory=True` and `skip_context_files=True` (`tools/delegate_tool.py`), so a
subagent sees only the `goal` / `context` it is given. Two failure classes follow from that in real use:

1. **Subagent runaway and goal drift.** With a thin brief and a large iteration budget, a subagent loops on the same
   failing command, scans ports, replaces working code with stubs to get a clean run, and reports success without
   checking the real end state.
2. **Zombie straggler overwrite.** After the main agent changes approach, a slower subagent that was never stopped
   finishes late and overwrites the working result of a faster one.

Hard caps on the main agent make both worse: cut off too early, it delegates before it understands the problem,
often with an empty `context`.

## 2. Design

| Concern | Mechanism |
|---|---|
| Keep the main agent responsive without starving it | Soft/hard turn budget by time and tool-output volume (60 s / 120 s by default), with a reminder before anything is frozen. Long foreground commands move to the background. |
| Delegation contract | `pre_tool_call` on `delegate_task`: thin or risky-without-rollback briefs are returned once per turn; accepted briefs get a chat summary, user memory, working rules and a default `output_schema` via `action: "modify"`. |
| Bounded workers | One plugin-side step budget per worker (`worker_max_tool_rounds`), plus fail-fast on repeated identical steps or failures. Hermes ignores a caller-supplied `max_iterations`, so the budget is enforced in the worker's own hooks. |
| No silent regressions | Working rules forbid stubbing or deleting working code, and results are checked for evidence (`subagent_stop`) before the main agent reports them. |
| Cancel on pivot | A new task whose paths, hosts or domains overlap a running worker is returned once with the `delegate_task(action='stop', ...)` call to make. |
| Write lease (optimistic concurrency) | A worker's `write_file` / `patch` is refused when the file changed after the worker started and the worker has not re-read it. |
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
