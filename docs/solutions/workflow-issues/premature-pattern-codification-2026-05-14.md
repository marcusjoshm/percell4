---
title: Don't codify a drift pattern as a convention until you've asked whether the design is wrong
date: 2026-05-14
category: workflow-issues
module: development-workflow
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Three or more copies of a near-identical code pattern have accumulated"
  - "About to write a learning that says 'fourth site triggers extract' or 'N copies means dedupe'"
  - "The duplicated code participates in shared state, an audit invariant, or a downstream contract"
  - "The duplicated code is a workaround for something else — for example, local widgets that mirror canonical state"
  - "Codifying the pattern would name the workaround as the right way to do things"
related_components:
  - documentation
  - development_workflow
tags:
  - drift-class
  - learnings
  - conventions
  - retire-pattern
  - context-poisoning
---

# Don't codify a drift pattern as a convention until you've asked whether the design is wrong

## Context

On the morning of 2026-05-13, after three near-identical copies of a
channel-override widget had accumulated across PerCell4's Cellpose,
Grouped Segmentation, and FLIM panels, a learning was written:
[`docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md`](../conventions/panel-channel-override-pattern-2026-05-13.md).
It codified a "fourth site triggers extract into a shared
`_channel_combo.py` helper" rule, presented as a clean three-vs-four
extraction threshold.

The learning was correct in form. It correctly identified the drift
class, named the canonical implementation, scoped the extraction, and
matched the project's stated `_resource_name_prompt.py` /
`_stitching_flim_form.py` private-utility convention.

It was retired **the same afternoon**, eight hours later.

The next `/ce-brainstorm`
([`docs/brainstorms/2026-05-13-session-selection-window-requirements.md`](../../brainstorms/2026-05-13-session-selection-window-requirements.md))
identified three failure modes from user testing — divergent truth,
cross-feature leakage, mask/seg selection friction — that the override
pattern itself caused. The right fix wasn't "extract into a shared
helper" (which the convention recommended); it was "retire the override
pattern entirely and consolidate at a canonical surface" (a new
`SessionWindow` owning the Selectors). PR #12 shipped that consolidation
the next day and marked the convention `superseded` with back-references
to the requirements doc and plan.

The convention's lifespan: ~8 hours.

## Guidance

When you're about to write a learning that codifies a drift pattern
("three sites, fourth triggers extract" / "N copies means dedupe a
helper"), pause and ask:

> *Is the duplicated code participating in shared state, an audit
> invariant, or a downstream contract that other features depend on? If
> so — is the duplication itself a signal that the underlying design is
> wrong?*

If the answer is "yes, it's mirroring canonical state that lives
somewhere else," the convention should not codify the workaround. The
right move is to surface the design question (in `/ce-brainstorm` or a
plan) before writing the convention. A learning that names the workaround
as the right way to do things will steer future agents toward replicating
it, when the corrective fix is a different shape entirely.

Concrete checklist before writing a "drift class → extract" convention:

1. **What canonical state does the duplicated code touch?** If it
   mirrors a session field, dataset metadata, or another already-owned
   state surface, the duplication may be the bug.
2. **Do the copies write back to the canonical state, or do they read
   only?** Read-only-but-pretending-to-be-authoritative is the signature
   shape of "Action masquerading as Selector"
   ([see related](../architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md)).
3. **Do other features read the canonical state directly?** If yes,
   the override silently breaks them. The convention can't fix this
   class of bug; only a redesign can.
4. **Does the project already have an audit invariant that names "who
   may write this"?** Re-read the invariant. The right answer is often
   already encoded in it.
5. **If none of the above applies** — i.e., the duplication is genuinely
   structural and Action-shaped — extract is fine. The
   [sibling-dialog-extract learning](../architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md)
   is the canonical example of when extraction is right.

## Why This Matters

The point isn't that drift-class conventions are bad — many are
correct and durable (the `_stitching_flim_form` extraction in PR #9 still
holds). The point is that **a written convention has a long blast
radius**. A learning saying "fourth site triggers extract" steers every
future agent toward the extraction — including agents who would have
asked "should this exist at all?" if the workaround weren't already
documented as canonical.

This compounds in two ways:

- **Context poisoning.** The user's persisted preference is for lean
  docs and aggressive archiving; coexisting contradictory rules drift
  apart and confuse future readers (auto memory [claude]).
- **Recency-of-decision tax.** When a convention codifies a recent
  decision and that decision turns out to be wrong, every doc that
  cites the convention is now subtly stale. The wider the convention is
  cited, the larger the retraction surface.

The cheapest move is to wait one design cycle before codifying. The
panel-channel-override learning would have caught the right answer
*automatically* if it had waited until after the next brainstorm —
because that brainstorm was triggered by the same observable failures
that prompted the convention in the first place.

## When to Apply

Wait on writing the convention when:

- The duplicated code references **shared mutable state** (session
  fields, dataset metadata, global config).
- An **audit / invariant doc** already covers the field being touched.
- **Downstream features read the same state directly** — i.e., the
  duplication exists in a feature that's coupled to other features
  through the state it manages.
- You're **about to invoke the "extract" verb without first asking
  "retire"**. If you haven't considered retirement as an option, you
  haven't done enough thinking yet.

Write the convention as planned when:

- The duplicated code is **purely structural** (widget construction,
  layout boilerplate) and doesn't carry state across module boundaries.
- The pattern has **survived at least one design cycle** of user
  testing or review without surfacing failure modes that would change
  the underlying design.
- Retirement was **explicitly considered** and rejected with a recorded
  rationale.

## Examples

**WRONG — codify a drift pattern that mirrors canonical state.**
The morning of 2026-05-13: write
`panel-channel-override-pattern-2026-05-13.md` after PR #11 lands the
third copy of `_channel_combo` + `update_channels`. The learning
specifies the canonical extraction helper signature
(`update_channels_combo(combo, session, viewer_getter)`), the file path
for the helper (`src/percell4/gui/_channel_combo.py`), and the rule
that the fourth site triggers refactor. The learning is internally
consistent, well-cited, and structurally well-formed.

It is also wrong, because it codifies a pattern that has no business
existing. The fact that the override widget never writes back to
session was a tell: an Action-shaped widget claiming a Selector's
responsibility. The right answer is one canonical Selector surface,
not N extracted Selector-shaped Actions.

**RIGHT — surface the design question before codifying.**
The afternoon of 2026-05-13: instead of writing the convention, write
the question into a `/ce-brainstorm` prompt — "I have three modules
with local overrides for the same canonical session field; should I
extract a shared helper, or is the override pattern itself the
problem?" The resulting brainstorm produces the requirements doc that
becomes PR #12.

The cost of waiting was small (a few hours). The cost of not waiting
was a written convention that had to be retired same-day, plus a new
learning to document the retirement, plus the cognitive load on every
future agent who reads the superseded doc and has to follow the
back-reference chain to understand which guidance is current.

## Related

- [`docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md`](../conventions/panel-channel-override-pattern-2026-05-13.md)
  — the canonical example of this anti-pattern. Codified a drift class
  that should have been retired instead. Lifespan ≈ 8 hours.
- [`docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`](../architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md)
  — the architectural learning that replaced it. Pairs with this
  workflow learning: this one is about *when not to write a
  convention*; that one is about *the design move the convention
  would have prevented*.
- [`docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`](../architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md)
  — a drift-class learning that *was* the right call. Useful contrast:
  the sibling-dialog case extracts code that is genuinely Action-shaped
  (widget construction tied to a dataclass), not state-bearing.
- PR #12 (squash `8547629` on `main`, merged 2026-05-14) — the
  consolidation that retired the over-codified convention.
