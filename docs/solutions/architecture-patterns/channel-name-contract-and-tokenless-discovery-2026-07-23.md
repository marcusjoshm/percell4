---
title: "Channel-name contract and tokenless named-channel discovery"
date: 2026-07-23
category: architecture-patterns
module: src/percell4/domain/io
problem_type: architecture_pattern
component: tooling
severity: medium
related_components:
  - "src/percell4/adapters/importer.py"
  - "src/percell4/gui/compress_dialog.py"
  - "src/percell4/gui/workflows/single_cell/config_dialog.py"
  - "src/percell4/domain/io/discovery.py"
  - "src/percell4/domain/io/tokenless.py"
applies_when:
  - "Mapping a channel token to a UI or stored name anywhere in the import/display pipeline"
  - "Writing or reading /metadata.channel_names in the HDF5 store"
  - "Adding a producer or consumer that must agree on the channel-name string form"
  - "Importing flat folders of name-suffixed TIFFs with no chXX token"
  - "Synthesizing structure (a regex/config) that discovery and a downstream re-parse must both use"
tags:
  - channel-naming
  - single-source-of-truth
  - producer-consumer-contract
  - tokenless-import
  - tiff-discovery
  - regex-synthesis
  - hdf5-metadata
  - io-pipeline
---

# Channel-name contract and tokenless named-channel discovery

## Context

The TIFF → HDF5 import pipeline needs a *channel name* for every channel it writes
into `/metadata.channel_names`. That string crosses at least three layers: the
importer that produces it (`adapters/importer.py`), the batch compress dialog that
displays it (`gui/compress_dialog.py`), and the single-cell workflow config dialog
that re-derives it to build thresholding-round dropdowns and per-dataset specs
(`gui/workflows/single_cell/config_dialog.py`). Downstream, `threshold_compute`
looks channels up by exact name against what the importer stored.

Two failure modes motivated this learning:

1. **A cross-layer string was derived independently in ~8 places.** Scattered
   `f"ch{ch_id}"` literals meant the producer and a consumer could drift. They did:
   the importer wrote `"ch02"` while the workflow config dialog stored the raw token
   `"02"`, and `_channel_index(store, "02")` raised `KeyError: channel '02' not in
   dataset; available: ['ch00','ch01','ch02']` — *after* a multi-minute Cellpose
   segmentation pass had already run for every dataset. (See
   `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`.)
   The same anti-pattern recurred a month later in a *different* consumer — the
   Compress dialog's registration Reference-channel dropdown was seeded from raw
   `f"ch{ch}"` IDs built before the Manual rename panel and never reconciled with
   the renamed names, so registration failed with `reference_channel 'ch00' not
   found among the imported channels ['ER','G3BP1']`. *(session history)*

2. **A new discovery mode (tokenless, name-suffixed TIFFs like `..._SG_mask.tif`)
   risked becoming a parallel pipeline** that re-derived channel identity differently
   from the importer's re-parse.

Both were solved by collapsing to a single shared artifact: one canonical name
function, and one synthesized `TokenConfig` returned from discovery so the importer
re-parses the *identical* regex.

## Guidance

### (A) One canonical function owns the channel-name string

`channel_display_name(token)` in `src/percell4/domain/io/naming.py` is the single
source of truth. It is pure domain (stdlib only) and its rule is tiny:

```python
def channel_display_name(token: str) -> str:
    if not token:
        return "ch0"          # bare/single unnamed channel
    if token.isdigit():
        return f"ch{token}"   # numeric legacy form, byte-identical ("00" -> "ch00")
    return token              # name token verbatim ("DNA", "SG_mask")
```

Every producer and consumer calls it instead of formatting the string itself.

```python
# adapters/importer.py (producer, ~line 292)
# before:  default_name = f"ch{ch_key}"
default_name = channel_display_name(ch_key)

# config_dialog.py :: _derive_tiff_pending_channel_names (consumer)
# before:  out.append(name or ch_id)
out.append(name or channel_display_name(ch_id))

# compress_dialog.py (all ~7 display + config sites)
# before:  QListWidgetItem(f"ch{ch}")  /  QCheckBox(f"ch{ch}")  /  QLineEdit(f"ch{ch}")
QListWidgetItem(channel_display_name(ch))
```

The rule for numeric tokens is *byte-identical* to the retired `f"ch{token}"`
literals, so this is a pure de-duplication — no stored-data migration, no behavior
change for existing numeric imports. A user-typed name in a `LayerAssignment.name`
always overrides the derived default; the helper is only the fallback. Known
limitation: an all-digit *name* token (e.g. a wavelength `488`) renders `ch488`;
Manual rename is the escape hatch.

### (B) A value-agnostic pipeline is extended by synthesizing config, not forking code

`discover_tokenless` (`src/percell4/domain/io/discovery.py`) does **not**
re-implement scanning, grouping, or channel routing. The whole scan/discovery/import
pipeline is already *value-agnostic about the channel token* — `FileScanner` returns
`match.group(1)` for whatever channel regex it is given, and `discover_flat` groups
by stripping that same regex. So tokenless is a thin auto-config layer:

```python
def discover_tokenless(root, output_dir=None):
    scan = FileScanner(_NO_TOKENS).scan(path=root)     # enumerate files, parse nothing
    stems = [f.path.stem for f in scan.files]
    names, _ = derive_channel_names(stems)             # 1. derive vocabulary structurally
    if not names:
        return [], None
    pattern = build_channel_pattern(names)             # 2. synthesize longest-first regex
    token_config = TokenConfig(channel=pattern, timepoint=None, z_slice=None, tile=None)
    datasets = discover_flat(root, token_config, out)  # 3. reuse the existing grouper
    return datasets, token_config                      # 4. RETURN the config
```

Step 4 is load-bearing: the synthesized `TokenConfig` is returned so the caller
threads the *same* regex into `import_dataset`. The compress dialog caches it and
returns it from `_current_token_config()` whenever tokenless mode is active, so
discovery and the importer's re-parse agree byte-for-byte. **Never re-derive the
vocabulary independently on the import side.**

**The consistency rule inside `derive_channel_names`** keeps a multi-underscore
channel name whole against the naive "channel = segment after last `_`" split. For
`..._3x4_SG_mask`, the provisional split yields channel `mask` and orphans that file
into a singleton group `..._3x4_SG` (`{mask}`), while its sibling group `..._3x4`
carries the richer `{cells, DNA, G3BP1}`. The rule detects that a group is a proper
underscore-prefixed *descendant* of a strictly richer ancestor, re-absorbs it, and
prepends the intervening segment (`mask` → `SG_mask`). Correct vocabulary yields
rectangular groups (every group carries the same channel set); a too-shallow split
leaves a tell-tale ragged singleton. The `len(channels) > g_count` guard (strictly
greater, not `>=`) prevents merging two genuinely distinct equal-sized datasets that
merely share a prefix (e.g. `Washout_2` vs `Washout_20`).

`build_channel_pattern` then makes the regex substring-safe: escaped alternatives,
longest-first, end-anchored — `build_channel_pattern(["mask", "SG_mask"]) ==
r"_(SG_mask|mask)$"` so `SG_mask` wins over the substring `mask`.

## Why This Matters

- **The failure was maximally expensive and maximally delayed.** A one-character
  difference (`"02"` vs `"ch02"`) survived compilation, the compress step, and
  segmentation, surfacing only at `threshold_compute` — after minutes of Cellpose
  work per dataset. A single source of truth makes that divergence structurally
  impossible: producer and consumer format from the same function.
- **This anti-pattern recurs.** It has appeared at least three times in this
  codebase — the tiff-pending consumer (2026-05-21), the mosaic-merge
  reference-channel dropdown (2026-06-25, *session history*), and is the general risk
  any new channel-name site carries. Centralizing removes the whole family at the
  channel-name boundary.
- **A parallel discovery path would have re-introduced the same class of bug** at
  the channel-identity boundary. Returning the synthesized `TokenConfig` and reusing
  `discover_flat` + `import_dataset` means exactly one regex and one grouping
  algorithm; discovery↔importer parity holds by construction, not by matching two
  hand-written implementations (the "discovery scopes, processing consumes" rule of
  `docs/solutions/logic-errors/batch-compress-development-lessons.md`).
- **The contract is pinned by tests, not by convention.**
  `tests/test_gui_workflows/test_channel_name_derivation.py` asserts the consumer's
  derived names equal `store.metadata["channel_names"]` for *both* name tokens and
  numeric tokens — the real producer/consumer round-trip through an HDF5 file, not a
  mock. (This closes the pin-test prevention item the 2026-05-21 doc left open.)

## When to Apply

- **Adding a channel-name producer or consumer.** Any code that shows, stores, or
  looks up a channel name from a token must call `channel_display_name(token)` —
  never format `f"ch{token}"` or pass the raw token through. If you add a consumer,
  add or extend a contract test asserting it matches `store.metadata["channel_names"]`.
- **Extending filename-token parsing** (a new naming convention or suffix scheme).
  Prefer deriving a vocabulary and synthesizing a `TokenConfig` that the *existing*
  scanner/discovery/importer consume unchanged, over writing a new scan/group/import
  path. Return the synthesized config so the same regex is reused downstream.
- **Any structural discovery that must agree with a downstream re-parse.** When one
  component *infers* structure (grouping, splitting, classifying) and another later
  *re-derives* it, make the first return the artifact (config/regex/mapping) and have
  the second consume it — rather than running two independent derivations and hoping
  they agree.

Generalized principles worth carrying to unrelated code:

1. **Single source of truth for a cross-layer string contract.** Extract one pure
   function and route every site through it. Byte-identical legacy behavior makes the
   refactor a no-op for existing data. (Instance of
   `docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`.)
2. **When a pipeline is value-agnostic, extend it via synthesized config, not a
   parallel code path.** If the existing machinery only cares about *a* regex/token
   (not its value), a new mode is a config-generation layer, not a fork.
3. **Return the config so producer and consumer share one artifact** — converting
   "do these two implementations agree?" into "there is only one implementation."

## Examples

**Before — scattered literals, silent divergence:**

```python
# importer.py (producer)
default_name = f"ch{ch_key}"                 # -> "ch02"
# config_dialog.py (consumer)
out.append(name or ch_id)                    # -> "02"   ← mismatch, KeyError at threshold_compute
```

**After — one canonical function, all sites delegate:**

```python
# importer.py:        default_name = channel_display_name(ch_key)       # "ch02"
# config_dialog.py:   out.append(name or channel_display_name(ch_id))   # "ch02"  ← agree
# compress_dialog.py: QCheckBox(channel_display_name(ch))               # "DNA" / "SG_mask" verbatim
```

**Consistency rule keeping `SG_mask` whole (`..._3x4_SG_mask`):**

```
provisional split (channel = after last "_"):
  group "..._3x4"     channels {cells, DNA, G3BP1}   ← rich, rectangular
  group "..._3x4_SG"  channels {mask}                ← ragged singleton, descendant of "..._3x4"

rule: "..._3x4_SG" == "..._3x4" + "_" + "SG" and parent is strictly richer
  → move file to "..._3x4", prepend intervening "SG":  mask → SG_mask

result vocabulary: ["DNA", "G3BP1", "SG_mask", "cells"]   (rectangular groups)
```

## Related

- `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`
  — the originating bug this contract generalizes. Its prevention rule ("keep the
  `f"ch{ch_id}"` f-string mirrored in two files") is **superseded**: both sides now
  delegate to `naming.py::channel_display_name`.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — the ancestor
  feature (`_derive_dataset_name` token-stripping, Layer Write Dispatch, and the
  "discovery scopes, processing consumes" rule that `discover_tokenless` instantiates).
- `docs/solutions/logic-errors/single-channel-bin-token-fallback-2026-05-25.md` —
  sibling "producer and consumer must agree on a token-derived name" case (`.bin`
  channel fallback).
- `docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md`
  — generalizes the "re-derive a canonical value in a second module" anti-pattern.
- `docs/solutions/architecture-patterns/channel-deletion-permanence.md` — the writer
  side of the channel-name lifecycle.
- Audit: `docs/audits/canonical-sources-matrix.yaml` row `channel-name-default-ch-prefix`
  — its `canonical_file` should move to `naming.py` and `applies_to` should grow to
  include `naming.py`, `compress_dialog.py`, `discovery.py`, `tokenless.py` (pending refresh).
- Plan: `docs/plans/2026-07-23-003-feat-tokenless-named-channel-import-plan.md`.
