---
date: 2026-04-29
topic: assessment-of-io-principles-audit-initiative
re: docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md
---

# Assessment: I/O Principles Audit & Remediation Initiative

Assessing the plan as written — not the codebase itself.

## Overall

The overall shape is solid and shows real planning maturity: clear actors, tiered scope, explicit non-goals, and a clean separation between "blocks planning" and "deferred to planning" in the open questions. The **user-seeded + AI-completes** hybrid is genuinely well-targeted — it uses each side for what it's actually good at. The matrix-as-currency, cell-citations-as-receipts framing is clean.

That said, there are a few things worth pushing on before the audit kicks off.

---

## Biggest Risk: Three Decisions Compound

Three decisions in the doc are each defensible alone but predict drift when combined:

1. A **living markdown doc** as the source of truth.
2. **Per-file × per-principle granularity**, which puts the matrix at roughly 100 cells.
3. **No enforcement automation in v1** — relying on PR-description citations and social discipline.

In a working tree described as having active churn across six I/O files, the combination is fragile:

- Markdown tables of ~100 cells are painful to scan and diff.
- "MUST update matrix in the same PR" is pure social discipline with no mechanical check.
- The threads model means the matrix is touched constantly, multiplying merge-conflict surface.

**Suggestion:** keep the matrix as YAML or JSON with a render-to-markdown script. Cell updates become mechanical, diffs become reviewable, and a future CI check has a parseable artifact to lint against. Even without enforcement in v1, structuring the data for v2 enforcement costs almost nothing now and is expensive to retrofit later.

---

## Audit Repeatability Has an Unresolved Tension

Success criteria state that "a fresh agent given this doc + the matrix template can re-run the audit and produce a comparable matrix." Two issues:

- The audit is **user-seeded** — different seeds yield different first-pass matrices.
- The AI rubric pass is **non-deterministic** on top of that.

The hybrid is the right call for completeness, but you're trading repeatability for it. The doc should **name that trade** rather than imply both repeatability and completeness come for free.

Relatedly, the doc never specifies **what state the audit runs against**. With six files mid-edit in the working tree, "audit current I/O code" is ambiguous: HEAD? Working tree? Post-WIP-commit? Pin this explicitly, or the first re-run won't be comparable to the second.

---

## Acceptance Rules That Will Bite

**AE3 is too strict as written.** It says a plan that addresses only some of a thread's cited cells "MUST be rejected as incomplete." In practice, one cell in a thread is often much harder than the others, and threads will stall on their hardest cell. Add an explicit escape hatch: a thread plan MAY defer cells with stated reason, deferred cells return to the matrix as-is, and the thread closes when its non-deferred cells go green.

**The matrix has no "partial" state.** Cells are OK / VIOLATION / N/A. A PR that fixes 70% of a cell's evidence has nowhere to land — it has to either claim full closure (untrue) or claim nothing (loses progress). R10 handles new violations surfacing mid-thread but not partial closures of existing ones. Consider adding a `PARTIAL` state with a required note pointing at residual evidence.

---

## Smaller Things Worth a Pass

- **First-thread density claim is unfalsified.** The rationale says TCSPC append is "the densest principle cluster" — but you can't know that until the matrix exists. Either soften the claim ("user-named pain, plausibly dense") or do a quick cell-count check before locking it in.

- **R11's ranking formula gives false precision.** `pain × cells ÷ complexity` is three estimated terms in a single number. "AI proposes a ranking with rationale, user decides" is more honest and is what will happen anyway.

- **Tests aren't tiered.** T1/T2/T3 doesn't say where I/O *tests* live. They can themselves encode violations (fixture paths, hardcoded conventions, format-token assumptions). Pick a tier for them explicitly.

- **No mechanism caps thread scope.** Once the planner has a thread and sees an adjacent matrix cell, "let's grab that too" is the natural drift. Add a rule: if a thread expands beyond N cited cells during planning, split it.

---

## What This Doesn't Block

None of the above blocks `/ce-plan` for Thread 1. The assessment that the requirements doc alone is enough to plan TCSPC append + cross-format token matching is correct — the seed examples, principles cited, and acceptance behaviors are all sufficient.

The two things worth resolving **before** the audit pass kicks off (cheap now, expensive later):

1. **Matrix format** — markdown vs. structured-with-render.
2. **Audit baseline** — which git state the audit runs against.

Everything else can be addressed as the matrix and first thread land.
