# Why your AI agent quietly gets worse

*A failure taxonomy from running agent fleets in production*

---

There is no shortage of material on building an AI agent. There is almost nothing on what happens six weeks later, when the agent is still running, still answering, and still wrong.

That gap matters because agent failures in production are not the failures people plan for. They are not "the model isn't smart enough". They are infrastructure failures wearing a costume. The agent doesn't crash. It degrades. And because it degrades silently, you find out weeks later from a user, from a wrong number in a report, or from the moment you finally open the database.

This is the taxonomy I've arrived at from operating multi-agent fleets — supervised processes, long-lived memory stores, scheduled jobs, message gateways — and from the incident write-ups that keep appearing in the same shape. Ten classes. Most of them are invisible until you go looking.

---

## 1. Memory corruption

The most under-served failure class in the entire ecosystem, and the one nobody ships a tool for.

Every framework now has "long-term memory". LangGraph, Letta, Mem0, MemGPT-style stores, a dozen vector backends. Almost none of them can tell you whether the memory is *correct*.

Four shapes:

- **Orphaned vectors.** The embedding write succeeds and the row write fails, or the reverse. Retrieval now returns memories that don't exist, or silently loses memories that do. `count` in your logs has quietly stopped matching reality.
- **Degenerate embeddings.** A failed or rate-limited embed call persists a zero or NaN vector. That memory is now unreachable forever. Nothing warns you. It is in the database. It will never be retrieved again.
- **Index divergence.** The vector index and the source-of-truth store disagree after a crash mid-write. The index is fast and the store is correct, so your reads are wrong in a way that looks like a ranking problem.
- **Poisoned memory.** An adversarial or accidental write makes the agent confident about something false. This one is now getting attention as a security concern, because memory poisoning turns an agent's own history into an attack surface.

**How you notice:** you usually don't. Which is why the check has to be a scheduled job rather than a debugging session.

## 2. Silent state loss

The class that taught me to stop trusting "the process is running".

A database in write-ahead-log mode with a set of stale holders, a state file that gets truncated, a migration that half-applied. The service is up. It accepts writes. It answers reads from cache. And the writes are going nowhere.

The tell is not a crash. The tell is that the message count stopped increasing four hours ago.

## 3. Crash durability

Long-running agents need durable execution: checkpoint, resume, idempotency keys, an append-only event log. Almost nobody builds it on the first version, because the first version works fine — right up until the process is restarted mid-task.

Without checkpointing, a restart means either losing the work or replaying it. Both are bad, and replaying is worse when the work had side effects.

## 4. Double execution

The corollary of #3. If a task is not idempotent and the runner retries after an ambiguous failure, you get the email sent twice, the payment taken twice, the record created twice.

This is the failure class that most reliably produces a real-world consequence, because it's the only one that escapes the machine.

## 5. Cost drift

Token spend is the one resource metric that degrades without a bound. A retry loop that doesn't terminate, a context window that grows every turn, a subagent fan-out that recurses — none of these throw, they just bill.

The fix is boring and effective: hard budgets per key, spend tracking, and an alert threshold that fires before the month ends, not after. Route-level limits beat dashboards, because nobody watches a dashboard.

## 6. Process supervision gaps

An agent fleet is a set of processes that are supposed to stay alive. The failure mode is a process that dies quietly — no crash log, no restart, no alert, because nothing was watching it.

If your supervision story is "I'll notice", you will not notice. It needs a heartbeat, a liveness check, and a restart policy, and it needs to be tested by killing the process on purpose.

## 7. Configuration rot

A config key that was renamed, so a reader silently fell back to its default. A value written as a quoted JSON string instead of a real list, so the loader parsed it as a string and every consumer ignored it. A feature flag that was set to true in one place and false in another.

Configuration failures do not announce themselves because *the system keeps working*. It just does the wrong thing, quietly, forever.

The defence is validation at load time: assert the shape of everything you read, and fail loudly on an unexpected type rather than defaulting.

## 8. Delegation misconfiguration

When you fan work out to subagents, the model choice is a correctness decision, not a performance one. A model that can't handle the reasoning parameter doesn't degrade gracefully — it errors, or worse, it returns something plausible that mangles the output.

The rule I've landed on: pin the model for the job, validate that it's reachable before dispatch, and smoke-test the pipeline whenever the configuration changes. "The config looks right" is not evidence.

## 9. Interface fragility at the edges

Everything that touches the outside world — a device integration, an auth handshake, a messaging gateway pairing — is where a working system stops working for reasons that have nothing to do with your code.

Two things I've learned: first, distinguish "the handshake succeeded" from "the work succeeded" — a device can complete its auth and then fail to answer the actual request, and those are different bugs. Second, when something wedges, always check whether a reboot clears it before you write a patch; rebooting the remote side resolves a surprising fraction of these and costs nothing.

## 10. Verification theatre

The meta-failure, and the one that hides all the others.

Reporting success because the build passed, because the process is running, because the exit code was zero, because the file was written. None of those are the thing you actually wanted to be true.

The discipline that fixes it: verify the *effect*, not the *action*. Read the value back. Hash the file before and after. Check that the write is visible to a reader that isn't the writer. If you can't observe the effect, you don't get to say it worked.

---

## What actually helps

None of this is solved by a smarter model. It's solved by instrumenting the boring parts.

1. **Check memory integrity on a schedule.** Not when you suspect a problem — on a schedule, so you find out before a user does.
2. **Bound everything that can grow.** Spend, retries, context, fan-out depth.
3. **Make restarts safe** by making work idempotent and checkpointed.
4. **Watch liveness, not just exit codes.** A heartbeat catches the failures that logs don't.
5. **Validate configuration at load.** Fail loudly on surprises.
6. **Verify effects, not actions.** Everywhere, always.

The through-line: **agents fail silently, so your instrumentation has to be the thing that talks.**

---

*I build and run agent fleets, and I got tired of there being no tool for class #1, so I'm building one — `memdoctor`, an integrity checker and repairer for agent memory stores. It's early and it's MIT licensed. If you've hit a failure mode that isn't on this list, I want to hear about it more than I want to hear agreement.*
