---
title: "feat: Tokenless named-channel batch import"
type: feat
status: completed
date: 2026-07-23
---

# feat: Tokenless named-channel batch import

## Overview

The batch-compress dialog (`CompressDialog`, titled "Compress TIFF Dataset") today
recognizes a channel only when the filename carries a numeric token like `_ch00`
(regex `_ch(\d+)`). Microscopy exports frequently name the channel instead —
`..._cells.tiff`, `..._DNA.tif`, `..._G3BP1.tif`, `..._SG_mask.tiff`. Those files
currently parse to **zero** channel tokens and cannot be compressed into `.h5`.

This feature adds a **Tokenless** import mode: the user points at a flat folder of
name-suffixed TIFFs, the app auto-derives the dataset grouping and the per-file
channel name **structurally** (no `chXX` token, no user-typed regex), and the
existing **Manual** mode remains the safety net for renaming a mis-derived channel
and assigning each as channel / mask / segmentation.

Confirmed split rule (user, 2026-07-23): within a group, the **shared leading
prefix is the `.h5` dataset name** and the **differing trailing remainder is the
channel name**. Multiple datasets in one flat folder are separated by their
distinct prefixes:

    CellProfiler_U2OS_60min_As_3x4_{cells,DNA,G3BP1,SG_mask}.tif   → 1 .h5, 4 channels
    CellProfiler_U2OS_90min_Washout_2_{cells,DNA,G3BP1,SG_mask}.tif → 1 .h5, 4 channels
    CellProfiler_U2OS_90min_Washout_4x4_{cells,DNA,G3BP1,SG_mask}.tif → 1 .h5, 4 channels

**Key architectural leverage** (from research): `FileScanner._parse_tokens`
returns `match.group(1)` for *whatever* the channel regex captures, and
`discovery._derive_dataset_name` groups by *stripping* the channel match. The whole
pipeline is already token-value-agnostic. Tokenless mode therefore reduces to two
pure functions — **derive the channel-name set** from the filenames and
**synthesize an internal channel regex** from it — fed into the existing flat
discovery + import path. The only cross-cutting code change is removing the
hardcoded `ch` prefix (`f"ch{...}"`) that assumes numeric tokens.

---

## Problem Frame

- **Who:** the microscopy researcher batch-compressing CellProfiler / name-suffixed
  TIFF exports where channels are identified by biological name, not `chXX`.
- **Today:** name-suffixed files parse to no channel token → not importable via
  batch compress. The only workaround is a hand-typed regex hidden under "Advanced:
  Token Patterns", which non-programmer users will not use and which still produces
  wrong channel names (`chDNA`) and mis-groups multi-underscore names (`SG_mask`).
- **Want:** an explicit, regex-free "Tokenless" mode that auto-derives groups and
  channel names, with Manual rename/type-assignment preserved.

---

## Requirements Trace

- R1. A new **Tokenless** import mode in the batch-compress window derives datasets
  and channel names from filenames with no `chXX` token and no user-typed regex.
- R2. Within a group, the shared leading prefix becomes the `.h5` name; the trailing
  remainder becomes the channel name (confirmed split rule).
- R3. Multi-underscore channel names (e.g. `SG_mask`) are derived as one channel and
  grouped with their siblings — **not** split into `SG` + `mask` and orphaned.
- R4. Multiple datasets in a single flat folder are separated into one `.h5` each.
- R5. In Auto sub-mode every derived channel is imported as an intensity channel;
  resource-type assignment (channel / mask / segmentation) happens in **Manual**
  sub-mode, unchanged.
- R6. Manual sub-mode lets the user rename any mis-derived channel; the renamed
  string is what is stored, byte-for-byte, in `/metadata.channel_names`.
- R7. Derived / renamed channel names are stored **verbatim** (`DNA`, `SG_mask`), not
  `ch`-prefixed (`chDNA`) — and every downstream consumer reads the identical string.
- R8. Existing numeric-token (`chXX`), Subdirectory, and Flat-Directory modes are
  byte-for-byte unchanged.

---

## Scope Boundaries

- **No** user-facing regex field for the tokenless flow. Names are derived
  structurally; the synthesized regex is an internal implementation detail (per
  institutional learning: "do not expose regex to non-programmer users").
- **No** automatic resource-type inference from channel name (e.g. name contains
  "mask" ⇒ Mask). Auto mode = all intensity; type is a Manual-mode decision (R5).
  Recorded as an explicitly rejected option below.
- **No** change to the numeric `chXX` / Subdirectory / Flat discovery behavior, the
  tile-stitching path, or the `.bin`/FLIM cross-format matcher (which legitimately
  assumes numeric channel tokens and is out of scope for plain-TIFF name import).
- **No** change to the HDF5 group layout or the `_write_layer` resource-type routing
  (`/intensity`, `/labels/<n>` int32, `/masks/<n>` uint8-binarized).
- **Grouping errors are not renameable.** Manual rename fixes a wrong channel *name*;
  it cannot re-assign a file to a different `.h5` group. The derivation's grouping
  correctness (U1's consistency rule) is what protects R3/R4; a genuinely
  un-groupable folder is surfaced, not silently mis-split (see Risks).

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/domain/io/scanner.py` — `FileScanner._parse_tokens` (`:67`) returns
  `match.group(1)`; already token-value-agnostic. **T1.**
- `src/percell4/domain/io/discovery.py` — `discover_flat` (`:83`),
  `_derive_dataset_name` (`:142`, strips the channel full-match to derive the group
  prefix), `discover_by_subdirectory` (`:19`). Tokenless grouping composes on top of
  this. **T1.**
- `src/percell4/domain/io/models.py` — `TokenConfig` (`:13`, `__post_init__`
  validates ≥1 capture group, `_MAX_PATTERN_LENGTH=200`), `DiscoveryMode` StrEnum
  (`:135`, currently `SUBDIRECTORY`/`FLAT`), `LayerType` (`:127`),
  `LayerAssignment` (`:272`), `DatasetSpec`/`CompressConfig` (`:280`/`:303`).
- `src/percell4/adapters/importer.py` — `import_dataset` (`:94`, accepts
  `token_config`, `files=`, `layer_assignments`); the hardcoded name at `:292`
  `default_name = f"ch{ch_key}" if ch_key else "ch0"`; `_group_by_channel` (`:1052`).
  **T1 — R15/R16 hook fires.**
- `src/percell4/gui/compress_dialog.py` — `CompressDialog`. Discovery combo
  (`:117`+), Auto/Manual radios (`:133`), `_current_token_config` (`:642`),
  `_run_discovery` (`:650`), `_populate_lists` (`:713`),
  `_build_manual_channel_panel` (`:788`), `compress_config` (`:430`). The `ch`-prefix
  display mirrors at `:458`, `:498`, `:739`, `:803`, `:808`, `:849`, `:882`. **T1.**
- `src/percell4/gui/workflows/single_cell/config_dialog.py` —
  `_derive_tiff_pending_channel_names` (`:144`, fallback `name or f"ch{ch_id}"` at
  `:167`): the **consumer** partner of the importer's stored channel-name form.
- `src/percell4/interfaces/gui/main_window.py` — `_on_import_dataset` (`:1059`) →
  `_run_batch_compress` (`:1084`) reads `dialog.compress_config` and calls
  `import_dataset(files=ds.files, token_config=…, layer_assignments=…)` per dataset.
- `README.md:77` — user-facing description of the Flat-Directory / Manual flow
  (contains a "switch the discovery mode to Manual" inaccuracy — Manual is the Mode
  radio, not the Discovery combo — to fix alongside).

### Institutional Learnings

- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — the direct
  ancestor. **Bug #4:** grouping = "everything in the filename that isn't a known
  token"; extend the derivation, don't expose regex. **Bug #3
  (discovery-scopes-processing-consumes):** pass `ds.files` into
  `import_dataset(files=…)`; never re-scan `source_dir` (in flat mode all
  `DatasetSpec` share one `source_dir` → N identical `.h5`). Frozen `DatasetSpec` vs
  mutable `DatasetGuiState` split for Manual renames.
- `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`
  — **the load-bearing contract.** The importer producer and the config-dialog
  consumer must agree on the stored `channel_names` string byte-for-byte, or the
  single-cell workflow explodes at `threshold_compute` after a multi-minute segment
  pass. This feature *deliberately* breaks the old `f"ch{ch_id}"` assumption, so the
  new canonical name form must be pinned in one shared helper and enforced by a
  regression test across both files. Treat this doc as *why the contract matters*,
  not as a naming rule to keep.
- `docs/solutions/logic-errors/single-channel-bin-token-fallback-2026-05-25.md` — a
  parser fallback the UI never surfaces is invisible; unrecognized files must be
  surfaced, not silently dropped to `unmatched`.
- `docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md` — the
  scoping-collapse recurrence; consume `ds.files`, test flat mode with 2+ datasets.
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` —
  binarize masks at the write boundary; never infer resource type by name; block
  same-name collisions across `/labels` ∪ `/masks` ∪ `channel_names`.
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`
  and `atomic-write-contract.md` — resource creation goes through `_write_layer`; the
  compress path already routes here — do not regress.
- **I/O Principle 3** (`docs/audits/io-principles-matrix.yaml`): filenames are a
  fragile, last-resort identity source. Acceptable here because filename *is* the only
  signal, but the derivation must be forgiving and the result user-correctable.

### External References

- None needed — the pattern is fully local (flat-discovery + token machinery already
  in-repo). No external research performed.

---

## Key Technical Decisions

- **Tokenless = auto-synthesize a channel regex, then reuse `discover_flat`.** Two
  new pure functions (`derive_channel_names`, `build_channel_pattern`) produce a
  `TokenConfig` whose channel pattern is an end-anchored, longest-alternative-first
  alternation of the derived names; the *existing* `discover_flat` +
  `import_dataset(token_config=…)` path consumes it. This maximizes reuse and
  guarantees discovery and the importer's re-parse agree (they run the same regex).
  *Rationale:* the importer re-parses tokens from `ds.files` via `token_config`
  (`importer.py:209`); a synthesized regex is the clean bridge from structural
  derivation to the regex-based importer without touching `import_dataset`'s scan.
- **Canonical channel-name form lives in one helper.** A single
  `channel_display_name(token)` in `domain/io` returns the token *verbatim* for a
  name token and `ch{token}` only for a purely-numeric (or empty) token. Both the
  producer (`importer.py:292`) and every consumer (`compress_dialog`,
  `config_dialog._derive_tiff_pending_channel_names`) call it, satisfying the
  byte-for-byte contract by construction. *Rationale:* one door beats a threaded
  boolean flag (fewer drift sites); the `isdigit()` discriminator is unambiguous for
  realistic tokens.
- **Multi-underscore correctness via a grouping-consistency rule** (U1): if a
  provisional group name equals `<another group name>_<X>`, the trailing split was
  too shallow — re-absorb those files into the shorter group with the channel
  extended by `X`. This makes `SG_mask` derive correctly *because* the correct
  vocabulary yields rectangular groups and the shallow split yields a ragged
  singleton. *Rationale:* robust on the confirmed structure without a full search;
  Manual rename covers any residual mis-derivation.
- **Surface as a third Discovery-mode entry, not a new tab.** The user floated "it
  *can* be a new tab" (permissive). The dialog already speaks "discovery mode"
  (Subdirectory / Flat); adding "Tokenless (by common name)" to that combo reuses the
  entire Auto/Manual + channel-panel surface with no dialog restructure. The
  free-text regex "Advanced: Token Patterns" group is hidden in tokenless mode.
  *(Alternative — a genuine `QTabWidget` tab — considered and rejected on
  cost/consistency; revisit only if the user prefers hard visual separation.)*
- **Other tokens (`_t`/`_z`/`_s`) default OFF in tokenless mode.** Arbitrary channel
  names risk false matches from the numeric token defaults; tokenless datasets in the
  motivating case have none. Kept as an internal default, not a new user control.

---

## Open Questions

### Resolved During Planning

- *Prefix or suffix is the channel?* → Trailing remainder = channel; leading shared
  prefix = group (user-confirmed 2026-07-23).
- *Explicit name list vs structural derivation?* → Structural ("tokenless"),
  user-directed; no regex, no typed list. Manual rename is the correction path.
- *Auto-infer resource type from name?* → No (R5); Manual-mode only. Rejected below.
- *New tab vs discovery-mode entry?* → Discovery-mode entry (see decision above).

### Deferred to Implementation

- **Exact derivation algorithm internals.** U1 specifies the required *outcomes*
  (test scenarios pin `SG_mask`, multi-dataset, ragged-group cases); the precise
  clustering/consistency implementation is chosen against those tests.
- **Degenerate-folder UX copy.** The exact warning string + surfacing widget for
  "cannot derive a consistent grouping" (single file, no common prefix, one channel
  only) is settled while wiring the dialog against real folders.
- **Whether tokenless should also work in Subdirectory mode** (channel-by-name within
  each subfolder). Deferred; the motivating case is flat. The synthesized-regex design
  makes this a later thin addition if wanted.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context,
> not code to reproduce.*

**Data flow (tokenless mode):**

    flat folder ──► derive_channel_names(stems) ──► {cells, DNA, G3BP1, SG_mask}
                          │
                          ▼
                 build_channel_pattern(names) ──► r"_(SG_mask|G3BP1|cells|DNA)$"   (internal)
                          │                         (escaped, longest-first, end-anchored)
                          ▼
     TokenConfig(channel=<pattern>, timepoint=None, z_slice=None, tile=None)
                          │
             ┌────────────┴─────────────┐
             ▼                          ▼
        discover_flat(...)        import_dataset(files=ds.files, token_config=<same>, …)
     (groups by stripping           (re-parses SAME regex → identical channels →
      the channel match →            channel_display_name(token) → verbatim names →
      prefix = .h5 name)             _write_layer routing unchanged)

**Derivation + consistency rule (directional):**

    provisional: channel := segment after the LAST '_'; group := stem minus that segment
    repeat until stable:
        if group G == (some other group H) + '_' + X:
            reassign G's files to H, channel := X + '_' + <their channel>
    # correct vocabulary ⇒ rectangular groups (every group has the same channel set);
    # a too-shallow split (mask vs SG_mask) leaves a ragged singleton ⇒ re-absorbed.

**Naming contract (one helper, all sites):**

    channel_display_name("00")      -> "ch00"     (numeric token: legacy form, R8)
    channel_display_name("")        -> "ch0"      (single unnamed channel)
    channel_display_name("DNA")     -> "DNA"      (name token: verbatim, R7)
    channel_display_name("SG_mask") -> "SG_mask"

**Mode → behavior matrix:**

| Discovery mode | Channel source | Group (.h5) name | Regex field shown |
|---|---|---|---|
| Subdirectory | `_ch(\d+)` (or user regex) | subfolder name | yes (Advanced) |
| Flat Directory | `_ch(\d+)` (or user regex) | stem minus tokens | yes (Advanced) |
| **Tokenless (new)** | **derived name suffix** | **shared leading prefix** | **no (hidden)** |

---

## Implementation Units

- U1. **Pure-domain channel-name derivation**

**Goal:** Derive the channel-name set + per-file channel assignment structurally from
a list of filename stems, robust to multi-underscore names, and synthesize the
internal channel regex.

**Requirements:** R2, R3, R4.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/io/tokenless.py` (`derive_channel_names`,
  `build_channel_pattern`; pure — numpy/stdlib only, no Qt/h5py per domain rules)
- Test: `tests/test_io/test_tokenless.py`

**Approach:**
- `derive_channel_names(stems) -> tuple[list[str], dict[str, str]]` returns the
  ordered unique channel names and a `{stem: channel_name}` map, applying the
  consistency rule (see Technical design) so `SG_mask` stays whole and its files
  group with siblings.
- `build_channel_pattern(names) -> str`: `re.escape` each name, sort
  **longest-first** (so `SG_mask` wins over any substring), join as
  `_(a|b|c)$`-style end-anchored alternation. Enforce `_MAX_PATTERN_LENGTH` (raise a
  clear domain error if the vocabulary is too large) so the resulting `TokenConfig`
  never trips `__post_init__`.
- Return enough for the caller to build `TokenConfig(channel=pattern, timepoint=None,
  z_slice=None, tile=None)`.

**Technical design:** *(directional — see High-Level Technical Design derivation
sketch; the consistency loop is the required behavior, not prescribed code.)*

**Patterns to follow:**
- Pure-function style of `domain/io/discovery._derive_dataset_name`.
- Capture-group + `_MAX_PATTERN_LENGTH` contract in `models.TokenConfig.__post_init__`.

**Test scenarios:**
- Happy path: the 3-dataset / 4-channel motivating set → names
  `[cells, DNA, G3BP1, SG_mask]`, every `SG_mask` file mapped to channel `SG_mask`.
- Edge (multi-underscore): `SG_mask` derived whole, **not** `mask`; its group prefix
  equals the siblings' prefix (`..._3x4`), not `..._3x4_SG`.
- Edge (single dataset, single channel): one common prefix, one channel → one group.
- Edge (no common prefix / single file): returns a degenerate result the caller can
  detect and surface (does not crash, does not silently emit a nonsense channel).
- `build_channel_pattern`: alternation is longest-first and end-anchored; names with
  regex metacharacters are escaped; a stem matches exactly one alternative.
- `build_channel_pattern`: over-long vocabulary raises a clear error (not a raw
  `re.error` later in `TokenConfig`).
- Property: for the motivating set the derived pattern, run back through
  `re.search`, reproduces the same `{stem: channel}` map (discovery ↔ importer parity).

**Verification:** `derive_channel_names` returns rectangular groups for the
motivating folder and keeps `SG_mask` whole; the synthesized pattern round-trips.

---

- U2. **Token-type-aware channel display-name helper (the naming contract)**

**Goal:** One canonical function that maps a channel token to its stored/display name
— verbatim for name tokens, `chXX` for numeric — used by producer and all consumers.

**Requirements:** R7, R8.

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/io/naming.py` (`channel_display_name(token: str) ->
  str`) — or co-locate in `tokenless.py`; pick one home and import everywhere.
- Test: `tests/test_io/test_channel_display_name.py`

**Approach:**
- `token.isdigit()` (and non-empty) → `f"ch{token}"`; empty → `"ch0"`; otherwise the
  token verbatim. This is the single source of truth for the `channel_names` string.

**Patterns to follow:**
- The existing `f"ch{ch_key}" if ch_key else "ch0"` semantics at `importer.py:292`
  (preserved exactly for numeric/empty tokens → R8).

**Test scenarios:**
- Happy path: `"00" -> "ch00"`, `"1" -> "ch1"`, `"" -> "ch0"`, `"DNA" -> "DNA"`,
  `"SG_mask" -> "SG_mask"`.
- Edge: mixed-case / underscore names returned untouched; no lowercasing.
- Regression anchor: numeric outputs are byte-identical to the pre-change literal so
  existing `chXX` datasets keep their exact names (R8).

**Verification:** Every current numeric-token name is unchanged; name tokens are
verbatim.

---

- U3. **`discover_tokenless` discovery entry**

**Goal:** A discovery function that composes U1 + the existing `discover_flat` to
return `DatasetSpec`s grouped by common prefix with `DiscoveredFile.tokens["channel"]`
set to the derived name.

**Requirements:** R1, R2, R4.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/domain/io/discovery.py` (add `discover_tokenless(root,
  output_dir=None)`; add `DiscoveryMode.TOKENLESS` in `models.py`)
- Modify: `src/percell4/domain/io/models.py` (`DiscoveryMode` enum)
- Modify: `src/percell4/domain/io/CLAUDE.md` (document the new entry — deferred to U7
  docs unit; note here for traceability)
- Test: `tests/test_io/test_discover_tokenless.py`

**Approach:**
- Scan the flat root, derive names (U1), synthesize the `TokenConfig`, then delegate
  to `discover_flat(root, token_config=<synthesized>, output_dir=…)` so grouping and
  `output_path` naming come from the proven path unchanged.
- Return both the `DatasetSpec` list and the synthesized `TokenConfig` (or attach the
  pattern so the dialog can thread the identical config to `import_dataset`).
- Surface a degenerate/un-derivable folder as a `ScanResult.warnings` entry or a
  typed result the dialog can display (learning: no silent drop).

**Patterns to follow:**
- `discover_flat` return shape and `_derive_dataset_name` grouping.
- **Discovery-scopes-processing-consumes:** the returned `DatasetSpec.files` are the
  authoritative per-`.h5` file set the importer will consume; do not re-scan
  `source_dir` downstream.

**Test scenarios:**
- Happy path: motivating flat folder → exactly 3 `DatasetSpec`, each with 4 files and
  channels `{cells, DNA, G3BP1, SG_mask}`; `output_path` basenames are the 3 prefixes.
- Integration (2+ datasets, scoping): each `DatasetSpec.files` contains only that
  dataset's 4 files (guards the "N identical `.h5`" recurrence).
- Edge (`SG_mask`): the `SG_mask` files land in the same `DatasetSpec` as their
  siblings, channel token `SG_mask`.
- Edge: folder with a lone file / no derivable grouping → empty or flagged result,
  no crash.

**Verification:** Motivating folder yields 3 correctly-scoped datasets; re-running
does not duplicate.

---

- U4. **Importer: verbatim channel names + name-token assignment flow**

**Goal:** Replace the hardcoded `ch` prefix with U2's helper so Auto-mode name
imports store `DNA` (not `chDNA`), and confirm `layer_assignments` keyed by a name
token route correctly to `/intensity` / `/labels` / `/masks`.

**Requirements:** R5, R6, R7.

**Dependencies:** U2.

**Files:**
- Modify: `src/percell4/adapters/importer.py` (`:292` default_name → U2 helper; audit
  `:591`/`:692` TCSPC naming for consistency but leave `.bin` numeric path behavior
  unchanged)
- Test: `tests/test_io/test_importer.py` (extend)

**Approach:**
- `default_name = channel_display_name(ch_key)` at `:292`. Manual assignment override
  path (`layer_name = assignment.name or default_name`) is unchanged — the `or
  default_name` fallback now yields verbatim names.
- No signature change: the synthesized `token_config` and `layer_assignments` already
  flow via existing params. `_group_by_channel` groups by the (now name) token
  unchanged.
- **Run `python3 scripts/learnings_applicability.py src/percell4/adapters/importer.py`
  before editing** (T1 hook).

**Patterns to follow:**
- `_write_layer` resource-type routing and mask binarization
  (`(array>0).astype(uint8)`) — do not alter.

**Test scenarios:**
- Happy path (Auto, name tokens): import the motivating dataset with a synthesized
  name-token `token_config`, no assignments → `/metadata.channel_names ==
  ["cells","DNA","G3BP1","SG_mask"]` (verbatim, no `ch`).
- Integration (Manual routing): `layer_assignments={"SG_mask": Mask, "cells":
  Segmentation}` → `SG_mask` written to `/masks/SG_mask` as binarized uint8; `cells`
  to `/labels/cells` int32; `DNA`/`G3BP1` to `/intensity`. (Covers the currently
  untested mask/seg routing gap.)
- Edge (rename): `layer_assignments={"DNA": Channel name="GFP"}` → stored channel name
  is `GFP`.
- Regression (R8): numeric-token dataset (`_ch00`/`_ch01`) → names still `ch00`,
  `ch01`; byte-identical to pre-change.

**Verification:** Name-token Auto import stores verbatim names; Manual name-token
assignments land in the correct HDF5 groups; numeric imports unchanged.

---

- U5. **CompressDialog: Tokenless discovery mode + name-aware display**

**Goal:** Add "Tokenless (by common name)" to the Discovery combo, wire it to
`discover_tokenless`, thread the synthesized `TokenConfig` into `compress_config`,
and replace the `f"ch{ch}"` display literals with U2's helper so lists, checkboxes,
and Manual rename defaults show `DNA` / `SG_mask`.

**Requirements:** R1, R5, R6, R7, R8.

**Dependencies:** U2, U3, U4.

**Files:**
- Modify: `src/percell4/gui/compress_dialog.py` (discovery combo + `_run_discovery`
  branch; `_current_token_config` returns the synthesized config in tokenless mode;
  display sites `:458`, `:498`, `:739`, `:803`, `:808`, `:849`, `:882` → U2 helper;
  hide the "Advanced: Token Patterns" group + disable `_t`/`_z`/`_s` in tokenless
  mode)
- Test: `tests/test_gui/test_compress_dialog_tokenless.py`

**Approach:**
- Add the combo entry; in `_run_discovery`, when tokenless, call `discover_tokenless`
  and cache the synthesized `TokenConfig` on the dialog; `_current_token_config`
  returns that cached config (so `compress_config.token_config` and per-dataset
  `import_dataset` receive the identical regex — discovery↔import parity).
- Auto sub-mode: channel list shows `channel_display_name(ch)` labels. Manual
  sub-mode: rename `name_edit` pre-fills `channel_display_name(ch)`; type combo
  unchanged; `compress_config` keys `layer_assignments` by the name token.
- Hide the regex group and the FLIM group's channel-token assumptions in tokenless
  mode; surface a degenerate-folder warning from U3 in `_ds_count_label`.
- **T1 hook fires** — consult learnings before editing.

**Patterns to follow:**
- Existing `_run_discovery` generation-guard and `_populate_lists` /
  `_build_manual_channel_panel` structure; frozen `DatasetSpec` vs mutable
  `DatasetGuiState` (never mutate discovery results on rename).

**Test scenarios:**
- Happy path: point the dialog at the motivating folder in Tokenless mode →
  `_all_channels == [cells, DNA, G3BP1, SG_mask]`, dataset list shows the 3 prefixes,
  channel labels are verbatim (no `ch`).
- Integration: `compress_config.token_config` is the synthesized name pattern and
  `compress_config.datasets` are correctly scoped (each `ds.files` = its 4 files).
- Manual: renaming `DNA`→`GFP` and setting `SG_mask`→Mask yields
  `layer_assignments={"DNA": Channel/GFP, "SG_mask": Mask/SG_mask}` keyed by token.
- Edge: switching Discovery mode back to Flat/Subdirectory restores the `chXX`
  labels and re-shows the regex group (mode toggle is clean).
- Edge: degenerate folder → count label shows the warning, Compress disabled.

**Verification:** A user selects Tokenless, sees `DNA`/`SG_mask` channels and 3
datasets, and (Manual) renames + assigns types — no regex ever shown.

---

- U6. **Channel-name consumer alignment + contract regression test**

**Goal:** Make `config_dialog._derive_tiff_pending_channel_names` use U2's helper (or
read `/metadata.channel_names` directly) so the single-cell workflow sees exactly the
importer's stored names, and pin the producer↔consumer contract with a test.

**Requirements:** R7.

**Dependencies:** U2, U4.

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  (`_derive_tiff_pending_channel_names` `:144`–`:167` → U2 helper; update the
  docstring that currently cites the `f"ch{ch_id}"` convention)
- Test: `tests/test_gui_workflows/test_channel_name_derivation.py` (extend) and a new
  cross-module assertion that `store.metadata["channel_names"]` (post-import, name
  tokens) equals what the config dialog offers
- Modify (if it hardcodes the prefix): `tests/test_gui_workflows/` fixtures asserting
  the old `ch` convention — update to the token-type-aware expectation

**Approach:**
- Route the consumer through the same helper as the producer so a name token yields
  the same string on both sides; keep the numeric path byte-identical (R8).

**Patterns to follow:**
- The 2026-05-21 learning's "single API contract spanning importer.py and
  config_dialog" and its "pin with a test asserting `store.metadata['channel_names']`
  equals what the dialog offers".

**Test scenarios:**
- Contract: after a name-token import, the config dialog's derived channel list equals
  `store.metadata["channel_names"]` exactly (`DNA`, `SG_mask`, …).
- Regression (R8): numeric-token dataset still derives `ch00`/`ch01` identically.
- Miss handling: a `_channel_index` lookup for an absent name fails loudly / suggests
  the nearest name rather than silently mis-indexing (per learning).

**Verification:** The single-cell workflow config dialog and the importer never
disagree on a channel name for either name or numeric tokens.

---

- U7. **Documentation**

**Goal:** Reflect the new mode in current-state docs and fix the README inaccuracy.

**Requirements:** R1 (discoverability).

**Dependencies:** U3, U5.

**Files:**
- Modify: `src/percell4/domain/io/CLAUDE.md` (`discover_tokenless` + the naming helper)
- Modify: `src/percell4/gui/CLAUDE.md` (`compress_dialog.py` line: add Tokenless mode)
- Modify: `README.md` (document Tokenless mode; fix the ":77" "switch the discovery
  mode to Manual" wording — Manual is the Mode radio, not the Discovery combo)

**Approach:**
- Current-state only, per the repo doc rules (no history, no plans in module docs).
- After merge, capture the tokenless grouping + channel-name contract via
  `/ce-compound` and add a `docs/audits/canonical-sources-matrix.yaml` row for the
  channel-NAME contract (the 2026-05-21 doc's Prevention section recommends exactly
  this).

**Patterns to follow:**
- Existing terse module-CLAUDE.md style.

**Test scenarios:**
- Test expectation: none — documentation only.

**Verification:** Module docs name the new mode/helper; README describes Tokenless and
no longer mislabels the Manual control.

---

## System-Wide Impact

- **Interaction graph:** `CompressDialog.compress_config` → `main_window._run_batch_
  compress` → `import_dataset` (per dataset) → `store` writes → `/metadata.channel_
  names` → `config_dialog._derive_tiff_pending_channel_names` → single-cell workflow
  channel dropdowns / `_channel_index`. The channel-name string is the shared contract
  across this whole chain (U2 centralizes it).
- **Error propagation:** un-derivable folders surface as a dialog warning (U3/U5), not
  a silent empty import; over-long derived vocabularies raise a clear domain error at
  `build_channel_pattern` before `TokenConfig` construction.
- **State lifecycle risks:** discovery-scopes-processing-consumes — each
  `DatasetSpec.files` must be the sole import input (guards the recurring N-identical-
  `.h5` bug). Frozen discovery vs mutable Manual-rename state preserved.
- **API surface parity:** the headless path (`batch_process_datasets.py`,
  `workflows/phases.compress_one`) calls the same `import_dataset`; because tokenless
  only synthesizes a `token_config`, those paths gain name-token support for free when
  handed the synthesized config (no new headless surface built now, but the door is
  open; GUI/batch parity convention noted).
- **Integration coverage:** the U4 Manual-routing test and the U6 contract test are
  the two cross-layer proofs unit tests of the pure functions cannot give.
- **Unchanged invariants:** numeric `chXX` naming (R8), Subdirectory/Flat discovery,
  tile stitching, `.bin`/FLIM cross-format (numeric-token) matching, `_write_layer`
  routing, and mask binarization are all explicitly unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `SG_mask` mis-split into `SG` + `mask`, orphaning files | U1 consistency rule + dedicated `SG_mask` test scenarios in U1/U3/U4/U5; longest-first alternation in `build_channel_pattern` |
| Breaking the `channel_names` producer↔consumer contract (multi-minute-late crash class from the 2026-05-21 learning) | Single U2 helper used by producer and consumer; U6 cross-module regression test; numeric path pinned byte-identical |
| Regressing the "N identical `.h5`" scoping bug | Reuse `discover_flat`/`ds.files` scoping; U3 2+-dataset scoping test; never re-scan `source_dir` |
| Numeric `chXX` datasets renamed accidentally | U2 preserves `ch{n}`/`ch0` exactly; R8 regression tests in U2/U4/U6 |
| Arbitrary names false-matching `_t`/`_z`/`_s` defaults | Tokenless mode disables those tokens by default |
| Un-groupable folder silently importing garbage | U3 surfaces a warning; U5 disables Compress; no silent `unmatched` drop |
| T1 files edited without consulting canonical sources | Run `scripts/learnings_applicability.py` before editing `importer.py`, `discovery.py`, `scanner.py`, `compress_dialog.py`, `config_dialog.py`; R15/R16 hook warns |

---

## Alternative Approaches Considered

- **User-typed comma-separated name list** (instead of structural derivation):
  rejected — the user explicitly chose "tokenless" auto-derivation with Manual rename
  as the correction path; a typed list is more friction and the learnings advise
  against pushing token authorship onto users.
- **Genuine `QTabWidget` tab** for tokenless: rejected on cost/consistency; the
  Discovery combo already models "how datasets are found." Revisit only if the user
  wants hard visual separation.
- **Teach `import_dataset` to trust `DiscoveredFile.tokens` and skip its re-scan:**
  rejected — larger change to a T1 file; the synthesized-regex bridge keeps discovery
  and import provably in agreement with no importer scan change.
- **Auto-infer resource type from channel name** (name contains "mask" ⇒ Mask):
  rejected for this iteration (R5); the user wants type assignment to stay a Manual
  decision. Could return later as a Manual-mode *pre-selection* convenience only.

---

## Sources & References

- Research: repo analysis of `compress_dialog.py`, `discovery.py`, `scanner.py`,
  `models.py`, `importer.py`, `config_dialog.py`, `main_window.py`, `store.py`.
- Learnings: `docs/solutions/logic-errors/batch-compress-development-lessons.md`,
  `.../tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`,
  `.../single-channel-bin-token-fallback-2026-05-25.md`,
  `.../add-layer-flat-discovery-duplicate-import.md`,
  `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`,
  `docs/audits/canonical-sources-matrix.yaml`, `docs/audits/io-principles-matrix.yaml`.
- User decision (2026-07-23): trailing remainder = channel; shared leading prefix =
  `.h5` group name; Manual mode is the rename safety net.
