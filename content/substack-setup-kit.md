# Substack setup kit

Everything below is written and ready to paste. Your job is only account creation and pasting.

---

## 1. Publication name

```
Silent Failures
```

**One-line description (Substack asks for this at signup):**

```
Reliability engineering for AI agents in production — what breaks, why it breaks quietly, and how to instrument for it.
```

**Why this name:** it is the thesis of every post in the plan. The whole beat is that agent failures do not announce themselves, and it is broad enough to cover supervision, cost, durability and memory without renaming the publication when the topic moves.

---

## 2. About page

```
Most writing about AI agents is about building them. This is about what happens six weeks later, when the agent is still running, still answering, and still wrong.

Silent Failures is about operating agent systems in production: memory stores that corrupt, processes that die without a log line, costs that drift, retries that double-execute, and the instrumentation that catches all of it before a user does.

I run multi-agent fleets in production — supervised processes, long-lived memory stores, scheduled jobs, message gateways. This is the failure taxonomy I wish someone had handed me, written down as I hit it.

Expect one substantial post a week. No news roundups, no hype, no "10 prompts that will change your life". Just the operational side, with the specifics left in.

If you have hit a failure mode that is not in the taxonomy, I want to hear about it more than I want to hear agreement.
```

---

## 3. First post

**Title:**

```
Why your AI agent quietly gets worse
```

**Subtitle:**

```
A failure taxonomy from running agent fleets in production
```

**Body:** paste `content/drafts/01-failure-taxonomy.md` (this repo), everything from the italic subtitle line onward. Substack's editor accepts pasted markdown and converts the headings.

**Tags (Substack allows up to 5):**

```
AI, Software Engineering, Machine Learning, Programming, Technology
```

---

## 4. Welcome email (Substack sends this on first subscribe — paste into Settings → Emails)

**Subject:**

```
Here's the taxonomy
```

**Body:**

```
Thanks for subscribing to Silent Failures.

The premise, in one line: AI agents fail silently. They don't crash, they degrade — and by the time a user notices, the damage has usually been compounding for weeks.

I write about the operational side of running agent systems, which is the part almost nobody documents. What actually breaks in production, why it breaks quietly, and what to instrument so you find out before your users do.

Your first post is here: <LINK TO POST 1>

One thing I'd genuinely like: if you have hit a failure mode that isn't in the taxonomy, reply and tell me. The list is more useful than any single fix, and I'd rather it be complete than flattering to my own experience.
```

---

## 5. What I do the moment you give me the URL

1. Wire the repo README to point at the publication (top and bottom).
2. Add the subscribe link to the `memdoctor` README so GitHub traffic converts.
3. Publish the Substack edition of post #1 and verify the rendered page.
4. Draft the Hacker News submission: title, the right URL, and the first comment that goes with it.
5. Set the cadence reminder so post #2 lands in 7 days.

---

## 6. Deliberately not doing yet

- **No paid tier.** Substack will nag you to enable payments. Leave it off. The plan monetises a hosted tier on the tool, not the newsletter — a paywall on a list of 40 people earns nothing and costs reach.
- **No recommendations/network farming.** Cross-promotion between new publications buys subscribers who don't read.
- **No cross-posting to Medium/dev.to yet.** Duplicate content splits what little SEO equity the posts have.
