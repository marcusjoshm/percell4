---
title: "feat: TCSPC append + cross-format token matching"
type: feat
status: active
date: 2026-04-29
origin: docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md
---

# feat: TCSPC append + cross-format token matching

> Thread 1 of the I/O principles audit-and-remediation initiative. Targets principles **3, 4, 5** primarily; corollary effects on **1, 6**.

## Overview

Today, TCSPC `.bin` data can be ingested only as part of an *initial* import that creates the dataset's `.h5` file. There is no way to add `.bin` decay layers to an existing dataset. Cross-format token matching (the rule that pairs `.tif _s00_ch00` with `.bin s1_ch1` despite mismatched padding and offset) has been implemented inside `src/percell4/adapters/importer.py` for the new-dataset path, but the rule is not surfaced anywhere the user can see or override, and it is not reusable from an append entry point.

This plan delivers an append flow: a new "TCSPC (.bin)" tab in `AddLayerDialog` that discovers `.bin` files, matches them to existing intensity channels in a chosen `.h5` via a configurable cross-format rule, lets the user inspect and override the binding, then streams decay arrays into the existing `.h5` with provenance.

The work also factors the existing cross-format match logic out of `importer.py` into a pure-domain function so both the initial-import and append paths use one canonical rule — operationalizing principle 5 ("Batch ≡ Incremental") and principle 4 ("Single write boundary").

---

## Problem Frame

The microscope the user works with exports two file types per acquisition with different conventions:
- TIFF intensity: zero-padded tokens, 0-indexed (`*_s00_ch00.tif`)
- `.bin` TCSPC: unpadded tokens, 1-indexed (`*_s1_ch1.bin`)

The user's workflow needs both formats to land in one `.h5` dataset, with the right `.bin` channel bound to the right intensity channel. Today the workflow only succeeds when the user runs the *initial* import with both formats present in the source directory; if the `.bin` files arrive later (a common acquisition pattern), the user has no way to add them — and even when both are present, the cross-format binding rule is invisible.

The seven I/O principles say:
- (#3) **Metadata Before Filename** — discovery should probe metadata first; filenames are a fallback. The cross-format rule today is filename-only and hard-coded inside the importer.
- (#4) **Two-Layer Postel + Single Write Boundary** — the importer should absorb convention chaos; the store should stay strict; one chokepoint owns canonicalization. The store has no append boundary; the chokepoint exists only for create-from-scratch.
- (#5) **Batch ≡ Incremental** — adding TCSPC at-import-time and adding-after-the-fact must be the same code path. Today they aren't (only at-import works).

This thread closes those gaps for the TCSPC + cross-format slice (see origin: `docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md`).

---

## Requirements Trace

- R1. (origin R8.a) Add `.bin` TCSPC data to an existing `.h5` dataset that already has TIFF intensity channels.
- R2. (origin R8.b) Reconcile mismatched token conventions between formats so the right `.bin` channel binds to the right intensity image channel automatically.
- R3. (origin R8.c) The reconciliation rule is **discoverable and configurable** — the user can see the mapping the importer chose and override it before committing.
- R4. (origin R8.d) TCSPC tile stitching consistent with the existing intensity tile stitching (reuse `assembler.py`).
- R5. (origin R7) Remediation closes specific matrix cells; PRs cite which cells they retire (see "Matrix-cell citations" below).
- R6. (origin R10) Matrix updates land in the same PR that retires the cells.

**Origin actors:** A1 (User/Lee Lab researcher), A4 (PerCell4 codebase).
**Origin acceptance examples:** AE3 (a thread spanning principles 3+4+5 must address cells across all three).

### Matrix-cell citations this thread retires

When PRs land, they will cite these cells (formal matrix doc may be created in parallel by the audit pass; cells claimed pre-emptively here):

- `adapters/importer.py × #4` (canonicalization extracted to domain boundary)
- `adapters/importer.py × #5` (initial-import and append now share the matching function)
- `domain/io/models.py × #1` (CrossFormatRule formalized as a typed registry-able structure)
- `domain/io/cross_format.py × #3, #4` (new — metadata-first matcher; canonicalization at boundary)
- `store.py × #4, #5, #6` (append boundary extends single-write-boundary; provenance recorded through same boundary)
- `application/use_cases/add_decay_to_dataset.py × #5, #6` (one pipeline; provenance is a payload)
- `gui/add_layer_dialog.py × #3, #5` (metadata-first match surfaced; append uses the one pipeline)

---

## Scope Boundaries

- **Not in scope: rewriting the initial-import path.** The existing uncommitted work in `importer.py` stands. We refactor the cross-format-match block into a pure function and call it from there; we do not redesign the importer.
- **Not in scope: matrix-doc creation.** That belongs to the audit pass running in parallel (origin F1). This thread cites cells; the matrix doc lands separately.
- **Not in scope: Capability Matrix as a top-level artifact.** Principle #1 has corollary effects here (CrossFormatRule formalized), but the full driver registry is a future thread.
- **Not in scope: round-trip export of decay payloads.** Principle #2 is silent for this thread; export work is a separate thread.
- **Not in scope: `.sdt` (Becker & Hickl) append.** `read_sdt` exists but is not wired into the importer today. Add later if needed; the design does not preclude it.
- **Not in scope: streaming-write threshold tuning.** Decay streaming already happens in `_write_layer`-equivalent code; we reuse the existing chunk shape (`(64, 64, full-T)`) and lzf compression.

### Deferred to Follow-Up Work

- Matrix doc seeding for files this thread doesn't touch — handled by the audit-pass thread.
- Capability-matrix-as-registry artifact — a separate thread once 2-3 readers/writers exist to populate it.
- Provenance schema versioning beyond v1 — handled when a second payload type begins writing provenance and a divergence appears.

---

## Context & Research

### Relevant code and patterns

- **Existing cross-format match logic** lives in `src/percell4/adapters/importer.py` (the uncommitted block around lines 187-440). Pairs `.bin` files with intensity channels using (a) explicit channel-token match, then (b) base-stem prefix/suffix fallback. This is the canonical behavior to extract into a pure function.
- **Layer Write Dispatch pattern** — established by `src/percell4/store.py` (`write_array`, `write_labels`, `write_mask`). Documented in `docs/solutions/logic-errors/batch-compress-development-lessons.md`. The append path follows the same shape: typed write, dtype/group placement owned by store.
- **Streaming decay write** — `DatasetStore.write_array(..., is_decay=True)` already uses lzf + `(64, 64, T)` chunks. Reuse for append.
- **TokenConfig + FileScanner + discovery** — `src/percell4/domain/io/{models,scanner,discovery}.py`. Already groups `.tif` and `.bin` into the same `DatasetSpec`. `_parse_tokens` returns `dict[str, str]` raw matches with no coercion — that's where padding/offset normalization needs to happen.
- **`assembler.py`** — already shape-agnostic; `assemble_tiles` works for any 2D array, including decay slices.
- **Existing AddLayerDialog tabs** — `src/percell4/gui/add_layer_dialog.py` has the QDialog + tab pattern; new tab follows that shape. Compress-dialog `_write_layer(name, layer_type, array)` shows the dispatch convention to mirror.

### Institutional learnings

- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — Layer Write Dispatch pattern; `DatasetSpec.files` is the source of truth (do not re-derive from `source_dir`).
- `docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md` — flat-discovery flow already returned each file across N datasets, fixed once for `.tif`. Apply the same lesson when the new tab adds `.bin` discovery: never re-derive files.
- `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md` — `.bin` dtype is uint16 by default, dim_order is YXT canonical, header bytes auto-detected. Stream tile-by-tile for big stacks. Token tile indices are 1-based; normalize at I/O boundary.
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` — different payload types must not collide on group names. Decay group is `/decay/<channel_name>`, distinct from `/intensity` and `/labels/`.

### External references

The 7 I/O principles (`docs/ideation/2026-04-29-io-principles-ideation.md`) cited GDAL Identify-then-Open and ffmpeg's demuxer/muxer as patterns for two-phase I/O. The cross-format rule here is a small instance of that pattern: identify the channel binding before opening the `.bin` file for streaming.

---

## Key Technical Decisions

- **CrossFormatRule lives in `src/percell4/domain/io/models.py` as a tagged-union of frozen dataclasses.** Canonical naming (used everywhere — code, docs, serialization tag): the **in-memory class names are `ZeroPadOffsetRule`, `BaseStemRule`, `ExplicitRule`, and `CompositeRule`**. Four concrete strategies in v1:
  - `ZeroPadOffsetRule(pad_width: int, offset: int)` — the user's microscope: `_s00_ch00.tif` ↔ `s1_ch1.bin` with `pad_width=2, offset=1`.
  - `BaseStemRule()` — `DatasetA.bin` ↔ `DatasetA_ch00.tif` via stem prefix match.
  - `ExplicitRule(mapping: dict[str, str])` — user-supplied bin→channel map from the dialog. **Keys are `str(Path.resolve())`** — absolute, normalized paths. The matcher calls `.resolve()` on every scanned `.bin` before lookup; case sensitivity follows the underlying filesystem (the user runs macOS APFS, default case-insensitive — accept that two `.bin` files differing only in case will collide; flag as ambiguous in `MatchResult`).
  - `CompositeRule(rules: tuple[CrossFormatRule, ...])` — tries each rule in order; first match wins; `unmatched` only if every rule fails. **This is what the existing importer at `adapters/importer.py:213-237` actually does** (explicit token match → base-stem prefix → reverse-prefix). U1's refactor therefore passes `CompositeRule(rules=(ZeroPadOffsetRule(pad_width=2, offset=1), BaseStemRule()))` from `importer.py` to preserve byte-identical behavior. The dialog's "Auto: zero-pad with offset" picker resolves to the same composite.

  HDF5 serialization shape (under `/metadata/cross_format_rule` attrs as a JSON-encoded string, NOT individual flat attrs — keeps `ExplicitRule.mapping` and nested `CompositeRule.rules` round-trippable): `'{"type": "ZeroPadOffsetRule", "pad_width": 2, "offset": 1}'` for simple rules, `'{"type": "CompositeRule", "rules": [{"type": "ZeroPadOffsetRule", ...}, {"type": "BaseStemRule"}]}'` for nested, `'{"type": "ExplicitRule", "mapping": {"/abs/foo.bin": "ch00"}}'` for explicit. Deserializer is a small `_RULE_REGISTRY` dict in `cross_format.py`. Why a frozen dataclass per variant: matches the convention of `TokenConfig`, `TileConfig`, `FlimConfig`; serializable; hashable; a future fifth strategy adds one variant + one registry row without rewriting callers.
- **Cross-format matching is a pure function in `src/percell4/domain/io/cross_format.py`** (new file). Signature: `match_bin_to_intensity(bin_files: list[Path], intensity_channels: list[str], rule: CrossFormatRule, token_config: TokenConfig) -> MatchResult`. `MatchResult` carries `bindings: dict[bin_path, channel_name]`, `unmatched: list[bin_path]`, `ambiguous: list[(bin_path, list[channel_name])]`. The dialog renders all three; the use case refuses to commit if `unmatched` or `ambiguous` is non-empty unless the user has overridden via `EXPLICIT`.
- **Append API on DatasetStore** — `append_decay_layers(layers: dict[channel_name, decay_array], provenance: dict[channel_name, ProvenanceRecord], force: bool = False) -> None`. Idempotent failure mode: raises `LayerAlreadyExists` if `/decay/<channel_name>` exists and `force=False`. The method is the single chokepoint for append; no other code writes to `/decay/*` outside `DatasetStore`. Why a method on the existing class rather than a new `AppendStore` subclass: the append is just another typed write, the dispatch is already in `DatasetStore`, splitting it would fork the chokepoint we just consolidated.
- **Use case `add_decay_to_dataset`** lives at `src/percell4/application/use_cases/add_decay_to_dataset.py`. Orchestration only — no direct h5py, no Qt, no dialog state. Returns an `AppendReport` (matched / unmatched / ambiguous + per-channel write status). Why a new use case file rather than extending `importer.import_dataset`: the entry point shape is different (existing-h5 in, decay-only out vs. source-dir-in, full-dataset-out); merging would re-introduce the batch-vs-incremental fork the principles forbid.
- **Provenance schema v1** for cross-format binding. Written under `/provenance/decay/<channel_name>` as HDF5 attrs:
  - `source_path` (str): absolute or repo-relative path to the source `.bin`.
  - `cross_format_rule` (str): the in-memory class name of the rule that produced the binding (`ZeroPadOffsetRule` / `BaseStemRule` / `ExplicitRule`), matching the serialization tag in `/metadata/cross_format_rule`.
  - `match_evidence` (str): JSON-serialized payload sourced from `MatchResult.bindings[bin_path].evidence` — a tagged dict `{"kind": "tokens", "bin_tokens": {...}, "intensity_tokens": {...}}` for token-based rules, or `{"kind": "stem", "matched_prefix": "..."}` for `BaseStemRule`, or `{"kind": "explicit"}` for `ExplicitRule`. Both `MatchResult` and the provenance writer share this schema; defined in `domain/io/cross_format.py` so U2 imports it.
  - `importer_version` (str): `percell4.__version__`.
  - `timestamp_utc` (str): ISO-8601 with `+00:00` suffix.
  - `content_sha256` (str): hex digest of the source `.bin` bytes.

  This is the seed of principle #6's wider rollout — schema changes for other payloads in later threads.
- **Cross-format rule is per-dataset, not global.** The user might have one project with two microscopes; each dataset gets its own rule. Storage: in `DatasetSpec` (transient, planning-time) and in `/metadata/cross_format_rule` of the `.h5` (persistent, post-write).
- **The rule recorded in `/metadata/cross_format_rule` and in per-channel provenance is the rule the user *selected* — not the materialized `ExplicitRule` the dialog builds at commit-time.** The dialog passes BOTH `selected_rule` (the dropdown choice) AND optional `bindings_override: dict[str, str]` (only the cells the user manually edited) to the use case. Provenance records `cross_format_rule = selected_rule.type` plus `match_evidence` (the binding details). When `bindings_override` is non-empty, provenance flags the affected channels with `manually_overridden: true`. This preserves the audit trail: six months later a reader can tell which bindings were auto-derived vs user-edited.
- **`/metadata/cross_format_rule` updates only when the user explicitly chose a different *base* rule** (the dropdown changed between writes). Per-binding overrides via `bindings_override` do NOT update the metadata-level rule. `CrossFormatRuleConflict` raises only on dropdown-level changes, never on per-binding edits — fixing the dialog/store contradiction the reviewer surfaced.
- **No new dependencies.** Everything uses h5py + numpy + qtpy already in the stack.

---

## Open Questions

### Resolved during planning

- **Where does CrossFormatRule live?** → frozen dataclass in `domain/io/models.py`; persisted in `.h5` `/metadata/cross_format_rule` attrs.
- **Where does the matcher live?** → pure function in new `domain/io/cross_format.py`. Both `adapters/importer.py` and the new use case call it.
- **Where does the append boundary live?** → `DatasetStore.append_decay_layers`. No new `AppendStore` class.
- **What happens when the rule produces ambiguous matches?** → use case refuses to commit; dialog surfaces the conflict; user resolves via `EXPLICIT` override.
- **Per-dataset vs. project-wide rule?** → per-dataset, persisted in `.h5` metadata.

### Deferred to implementation

- **Exact `MatchResult` field types.** Likely `dict[Path, str]` for bindings and `list[tuple[Path, list[str]]]` for ambiguous, but final shape pinned during U1.
- **Whether `force: bool` on `append_decay_layers` exposes a `force_overwrite` flag in the dialog or stays internal-only.** Implementation may discover users want a "replace decay" affordance; first PR ships internal-only, follow-up PR adds UI if needed.
- **TCSPC tile detection for `.bin` when token regex doesn't carry tile index.** If user files are `*_s1_ch1.bin` with no tile token, append flow falls back to single-tile-per-channel. Add tile parsing to the .bin path if the user's actual data uses tile tokens — verify against real fixtures during U3.
- **Whether to detect existing `/decay/<name>` BEFORE running the discovery pass (fail-fast) or AFTER (full report).** First pass: detect after discovery (better error message); revisit if performance hurts on large directories.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
Append flow (new):

  AddLayerDialog (TCSPC tab)
    │
    │  user picks: directory, .h5, rule
    ▼
  add_decay_to_dataset use case
    │
    ├─► FileScanner.scan(directory)            # principle 5: discovery is shared
    │
    ├─► DatasetStore(h5_path).list_groups("/intensity")
    │     → existing channel names
    │
    ├─► match_bin_to_intensity(bin_files,           # principle 3: metadata-first match
    │                           channel_names,
    │                           rule,
    │                           token_config)
    │     → MatchResult { bindings, unmatched, ambiguous }
    │
    ├─► [if unmatched/ambiguous and rule != EXPLICIT]
    │     return MatchResult to dialog → user reviews/overrides → resubmit with EXPLICIT
    │
    ├─► for each (bin_path, channel_name) in bindings:
    │     read_flim_bin(bin_path) → (H, W, T) decay
    │     assembler.assemble_tiles(...)   # if tile tokens present
    │
    └─► DatasetStore.append_decay_layers(    # principle 4: single write boundary,
            { channel_name: decay_array },   # principle 6: provenance through same boundary
            provenance={ channel_name: {...} },
            force=False)


CrossFormatRule (data):

  ZeroPadOffsetRule(pad_width=2, offset=1)
    "_s00_ch00.tif"  ↔  "s1_ch1.bin"

  BaseStemRule()
    "DatasetA_ch00.tif"  ↔  "DatasetA.bin"   (stem prefix match)

  ExplicitRule(mapping: dict[Path, str])
    { Path("foo.bin"): "ch00", ... }


Initial-import path (refactored):

  adapters/importer.py  (lines 187-440 today)
    │
    │  EXTRACTED:
    ▼
  match_bin_to_intensity(...)          # same function as append calls
    │
    ▼
  (rest of importer flow unchanged)
```

The shape is deliberate: append and initial-import share the matcher; only the orchestration above and the entry point differ. That's principle 5 in code form.

---

## Implementation Units

- U1. **Extract cross-format matching to a pure-domain function**

**Goal:** Lift the .bin↔channel matching logic from `adapters/importer.py` into a pure function in `domain/io/cross_format.py`, formalize `CrossFormatRule` as a frozen dataclass in `domain/io/models.py`, and refactor `importer.py` to call the new function.

**Requirements:** R2, R3, R5

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/io/cross_format.py`
- Modify: `src/percell4/domain/io/models.py` (add `CrossFormatRule`, `MatchResult`)
- Modify: `src/percell4/adapters/importer.py` (replace inline matching block with call to `match_bin_to_intensity`)
- Test: `tests/test_io/test_cross_format.py`

**Approach:**
- `CrossFormatRule` is a `Union`-flavored frozen dataclass (or a tagged enum-of-dataclasses): `ZeroPadOffsetRule(pad_width, offset)`, `BaseStemRule()`, `ExplicitRule(mapping)`. Keep all variants in one module so callers `import CrossFormatRule` and use a single name.
- `MatchResult` carries `bindings: dict[Path, str]`, `unmatched: list[Path]`, `ambiguous: list[tuple[Path, list[str]]]`.
- `match_bin_to_intensity(bin_files, intensity_channels, rule, token_config)` is pure: no I/O, no Qt, no h5py. Reads tokens from filenames via the supplied `TokenConfig`, applies the rule, returns `MatchResult`. Must NOT raise on unmatched/ambiguous — those are part of the result.
- Refactor `importer.py` lines 187-237 to call this function with `CompositeRule(rules=(ZeroPadOffsetRule(pad_width=2, offset=1), BaseStemRule()))` — preserving the existing token-then-stem-prefix cascade. The rest of the streaming/write code stays unchanged. **Snapshot test in U1's verification must use a fixture that exercises BOTH paths** (a `.bin` file that matches via token AND a `.bin` file that matches only via stem fallback) — otherwise the snapshot is silent on the cascade behavior. If the user's existing test fixture only exercises one path, U1 includes a synthesized fixture that exercises both.

**Patterns to follow:**
- `domain/io/models.py` existing frozen-dataclass conventions (TileConfig, FlimConfig).
- Pure-domain pattern from `domain/measure/` and `domain/segmentation/`.

**Test scenarios:** *(All scenarios in U1 cover R2, R3, R5.)*
- Happy path: `ZeroPadOffsetRule(pad_width=2, offset=1)`. Files `_s00_ch00.tif`, `_s00_ch01.tif`, `_s0_ch1.bin`, `_s0_ch2.bin` produce `{bin1: ch00, bin2: ch01}`.
- Happy path: `BaseStemRule()` with `Dataset_A.bin` and intensity channels `{Dataset_A_ch00.tif, Dataset_A_ch01.tif}` produces `{bin: ch00}` when only one channel matches stem; ambiguous when stem matches two.
- Happy path: `ExplicitRule(mapping={Path("x.bin"): "ch07"})` returns exactly that binding regardless of tokens.
- Edge case: empty `bin_files` → `MatchResult` with empty bindings, no error.
- Edge case: empty `intensity_channels` → all bin files in `unmatched`.
- Edge case: `ZeroPadOffsetRule(pad_width=2, offset=1)` when `_s99_ch99.tif` is in intensity but no `.bin` matches → unmatched is empty (no .bin to match), bindings empty.
- Error path: `match_bin_to_intensity` does NOT raise — verify it returns the result instead, even when rule produces no matches at all.
- Integration: refactored `importer.import_dataset` produces the same per-channel decay groups in the resulting `.h5` as the pre-refactor version on a recorded fixture (snapshot test on `list_groups("/decay")`).

**Verification:**
- `pytest tests/test_io/test_cross_format.py` passes.
- `importer.import_dataset` still ingests the user's existing test fixture (`.tif` + `.bin` mixed-convention dataset) and produces identical `/decay/*` group structure.

---

- U2. **`DatasetStore.append_decay_layers` with provenance**

**Goal:** Add an append boundary on `DatasetStore` that streams decay arrays into an existing `.h5`, idempotent on duplicate channel names, and writes a structured provenance record per layer through the same boundary.

**Requirements:** R1, R5, R6

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/store.py` (add `append_decay_layers`, `LayerAlreadyExists` exception; add `ProvenanceRecord` dataclass — possibly in `domain/io/models.py` if it's data; possibly inline in `store.py` if it's only HDF5-shape)
- Test: `tests/test_io/test_store_append.py`

**Approach:**
- `append_decay_layers(layers, provenance, force=False)` is the new method. Opens the file for write, validates each `channel_name`, raises `LayerAlreadyExists(channel_name)` if `/decay/<channel_name>` exists and `force=False`. Writes each decay array via the existing `write_array(path, array, attrs, is_decay=True)` path (so chunking/compression are unchanged). Writes provenance attrs to `/provenance/decay/<channel_name>` group attrs.
- Provenance keys: `source_path`, `cross_format_rule`, `match_evidence`, `importer_version`, `timestamp_utc`, `content_sha256`. v1; future additions append fields.
- Per-operation open/close stays — the method opens once, writes both the decay layer and its provenance record under one open handle (atomic per-channel; if power loss between channels, prior channels are intact).
- New cross-format rule field also written to `/metadata/cross_format_rule` attrs the first time `append_decay_layers` runs against a dataset (or on initial import after U1 is wired).

**Patterns to follow:**
- `DatasetStore.write_labels` / `write_mask` for typed dispatch.
- `DatasetStore.write_array(..., is_decay=True)` for chunk/compression rules.
- `domain/io/models.py` for `ProvenanceRecord` if treated as data.

**Test scenarios:** *(All scenarios in U2 cover R1, R5, R6.)*
- Happy path (Covers R1, R6): store with `/intensity` only; call `append_decay_layers({"ch00": np.zeros((4,4,32))})`; assert `/decay/ch00` exists with right shape, dtype, chunks; assert `/provenance/decay/ch00` attrs match the supplied record across all 6 fields.
- Happy path (Covers R6, persistence roundtrip): first call writes `/metadata/cross_format_rule` attrs matching the rule supplied; reading back via a fresh `DatasetStore` returns the same rule. Second call with the same rule does NOT overwrite `/metadata/cross_format_rule`. Second call with a DIFFERENT rule raises `CrossFormatRuleConflict` unless `force=True`.
- Happy path: append two channels in one call; both land; provenance for both lands.
- Edge case: empty `layers` dict → no-op, no error.
- Edge case: very large decay (e.g., 1024×1024×132 float32) → streamed write succeeds without OOM (verify by checking peak memory stays under array size). Mark `@pytest.mark.slow`.
- Error path: `/decay/ch00` already exists, `force=False` → raises `LayerAlreadyExists("ch00")`; existing decay untouched.
- Error path: `/decay/ch00` already exists, `force=True` → overwrites; new shape replaces old; provenance updated.
- Error path: provenance dict missing a key for a channel in `layers` → raises `ValueError("provenance missing channel: chXX")`.
- Integration: round-trip — initial import via U1's refactored importer → append via `append_decay_layers` for a second channel → re-open with `DatasetStore` → all channels present with correct provenance.

**Verification:**
- `pytest tests/test_io/test_store_append.py` passes.
- `h5dump` (or `h5py.File(...).visititems`) on a result file shows expected group structure: `/intensity`, `/decay/ch00`, `/decay/ch01`, `/provenance/decay/ch00`, `/provenance/decay/ch01`, `/metadata/cross_format_rule`.

---

- U3. **`add_decay_to_dataset` use case**

**Goal:** Orchestrate the append flow: discover `.bin` files, read existing intensity channel names, call the cross-format matcher, stitch tiles, stream decay via `append_decay_layers`, return an `AppendReport`.

**Requirements:** R1, R2, R4, R5

**Dependencies:** U1, U2.

**Files:**
- Create: `src/percell4/application/use_cases/add_decay_to_dataset.py`
- Test: `tests/test_use_cases/test_add_decay_to_dataset.py`

**Approach:**
- Function signature: `add_decay_to_dataset(h5_path, source_dir, token_config, tile_config, flim_config, rule, force=False, progress_callback=None) -> AppendReport`.
- `AppendReport` carries: `bindings: dict[Path, str]`, `written: list[str]`, `unmatched: list[Path]`, `ambiguous: list[tuple[Path, list[str]]]`, `errors: dict[str, str]`.
- Flow: scan source_dir → filter to `.bin` → open store → read existing channel names from **`store.metadata["channel_names"]`** (NOT `list_groups("/intensity")` — `/intensity` is a single 3D Dataset, not a Group; channel names live in metadata attrs per `store.py` lines 264-273 and `store.rename_channel` at lines 316-345) → call `match_bin_to_intensity` → if unmatched/ambiguous and selected_rule is not `ExplicitRule` and `bindings_override` is empty, return early with the report (dialog shows; resubmits with override) → pre-flight check: for every binding, test whether `/decay/<channel_name>` already exists; if any do and `force=False`, return early with `errors[channel] = "decay already exists"` so the dialog can warn before any I/O → else read each `.bin`, stitch tiles by channel via `assembler.assemble_tiles`, build provenance record, call `append_decay_layers`.
- Provenance content: source_path = `str(bin_path.resolve())`, cross_format_rule = `selected_rule.__class__.__name__` (the user's *dropdown* choice, e.g. `"ZeroPadOffsetRule"` — not the materialized `ExplicitRule` from the dialog), match_evidence = JSON dict from `MatchResult.bindings[bin_path].evidence` per the schema in Key Technical Decisions, manually_overridden = bool, importer_version = `percell4.__version__`, timestamp_utc = `datetime.now(UTC).isoformat()`, content_sha256 = SHA-256 of `.bin` bytes (computed via separate `hashlib.sha256(open(bin_path, "rb").read()).hexdigest()` pass — pragmatic double-read; for typical 16-512 MB `.bin` files the ~1s extra is acceptable; the "computed during streaming read" optimization in earlier drafts of this plan was infeasible without refactoring `read_flim_bin`'s `np.fromfile` C-path).
- No Qt imports. Pure Python + numpy + h5py-via-store.

**Patterns to follow:**
- Use case shape from `application/use_cases/segment_cells.py` — pure function, returns a result dataclass, takes a progress callback.
- `assembler.assemble_tiles` for the per-channel decay stitching (decay slice arrays plug directly into `assemble_tiles` since it's shape-agnostic).
- Streaming read pattern from `adapters/readers.read_flim_bin` (already lazy-friendly).

**Test scenarios:**
- Happy path (Covers R1, R2, R4): existing `.h5` with `/intensity` (4 channels), source_dir with 4 single-tile `.bin` files using `ZeroPadOffsetRule(pad_width=2, offset=1)`, all 4 match, all 4 land in `/decay/<name>` with provenance. AppendReport.written has 4 entries.
- Happy path (Covers R1, R4): 2x2 tiled `.bin` per channel (4 channels, 16 .bin files). Stitching produces 4 decay volumes; AppendReport.written has 4.
- Edge case (Covers R1): source_dir contains TIFFs alongside `.bin` files. Scanner picks up both; use case filters to `.bin`; TIFFs ignored (with no warning — they're not in scope here).
- Edge case (Covers R5): source_dir contains zero `.bin` files → AppendReport with empty bindings and a clear `errors["scan"] = "no .bin files found"`.
- Error path (Covers R1): `.h5` has no `/intensity` group → AppendReport with empty bindings and `errors["intensity"] = "no intensity channels in dataset"`.
- Error path (Covers R2, R3): rule produces ambiguous matches and rule is not `ExplicitRule` → use case returns early; report has `ambiguous` populated; nothing written.
- Error path (Covers R5, per-channel atomicity): one channel binding is fine, second binding's `.bin` is corrupt → first channel commits, second channel's error recorded in `errors[channel_name]`, AppendReport.written has 1 entry. (Per-channel atomicity from U2.)
- Integration scenario (Covers R1, R2, R4 — and AE3): end-to-end with a real fixture. Start with `.h5` produced by initial import (TIFF only). Run use case with the matching `.bin` directory. Re-open `.h5`. Assert `/decay/*` channels match `/intensity` channel names 1:1 and provenance is non-empty.

**Verification:**
- `pytest tests/test_use_cases/test_add_decay_to_dataset.py` passes.
- A real test fixture (TIFF + .bin set with mismatched conventions) round-trips: pre-existing `.h5` becomes a `.h5` with both intensity and decay, openable in the napari viewer's existing flow.

---

- U4. **AddLayerDialog: TCSPC (.bin) tab with surfaced and editable mapping**

**Goal:** Add a fourth tab to `AddLayerDialog` that lets the user pick a directory, choose a `CrossFormatRule`, see the matcher's decision in a preview table, override individual bindings, and commit the append via `add_decay_to_dataset`.

**Requirements:** R1, R2, R3, R5

**Dependencies:** U3.

**Files:**
- Modify: `src/percell4/gui/add_layer_dialog.py`
- Test: `tests/test_gui_workflows/test_add_layer_tcspc.py`

**Approach:**
- New tab "TCSPC (.bin)". UI elements:
  - Directory picker (recursive scan).
  - `CrossFormatRule` picker: dropdown with "Auto: zero-pad with offset", "Auto: base stem", "Manual mapping". Selecting Manual reveals the binding table immediately empty; selecting Auto runs match, populates the table, lets the user override row-by-row.
  - Token config: re-uses the existing TokenConfig editor pattern from the Discover-TIFFs tab.
  - FlimConfig: new minimal control (frequency, dim_order dropdown, dtype dropdown, x/y/t dims). Defaults from `FlimConfig` defaults; saved per-dataset to `/metadata/flim_config` on commit.
  - Preview table: rows = `.bin` files, columns = matched channel name (editable combobox of existing channels + "(unmapped)"). Below the table: counts of matched / unmatched / ambiguous. Accept button enabled only when `unmatched + ambiguous == 0`.
- On Accept: dialog calls `add_decay_to_dataset(..., selected_rule=<dropdown_choice>, bindings_override=<edited_cells_only>)` — sends BOTH the user's dropdown rule AND the dict of cells the user manually edited (empty dict when the user accepted Auto verbatim). The use case re-runs the matcher with `selected_rule`, then applies `bindings_override` to overwrite specific bindings. This preserves the user's *intent* in provenance — Auto choices are recorded as Auto, only edited cells are recorded as `manually_overridden=true`.
- Pre-flight conflict surfacing: when the matcher returns bindings, the dialog calls a fast `store.list_groups("/decay")` to see which channels already have decay. Any binding whose target channel already has decay is row-highlighted (theme.WARNING) with tooltip "Decay exists for this channel — Replace?" Each conflicting row gets a "Replace" checkbox. Accept is enabled only when no unmatched/ambiguous remain AND every conflicting row has Replace explicitly checked. Replace-checked rows pass through `force=True` per-channel.
- Worker invocation: invokes the use case on a `QThread` worker via `gui/workers.py:Worker`. The worker constructor receives a callback `lambda msg: worker.progress.emit(msg)`; that callback is passed as the use case's `progress_callback`. (Existing `Worker.run` calls the callable synchronously and emits `finished` on return; per-step progress requires this callback→signal bridge — the existing pattern used by Cellpose elsewhere in the GUI.)
- After append succeeds: updates the in-process `DatasetStore` view; emits **`Event.DATASET_CHANGED`** via `Session` so the napari viewer reloads the decay layers (existing ripple — `StateChange` does NOT have a `layers` field today, and adding one is out of this thread's scope; using `DATASET_CHANGED` accepts heavier reload but stays inside the existing API). Tracked as a follow-up under "Reviewer Findings — Deferred" below if granular reload is needed.
- On error: shows the failed channels with their error messages from `AppendReport.errors`; the successful channels still committed.

**Patterns to follow:**
- Existing `AddLayerDialog` tab structure (Single TIFF, Discover TIFFs, ImageJ ROI, Cellpose .npy).
- `compress_dialog.py` for the discover→preview→accept UI shape (manual mode in particular — same dual-list pattern).
- `gui/workers.py` for `QThread` worker shape and signal contract.
- `state_changed` ripple via `CellDataModel` (per `src/percell4/CLAUDE.md`).

**Test scenarios:**
- Happy path (Covers R1, R2, R3): "Auto: zero-pad with offset" selected, directory picked, table populates with 4 bindings, Accept commits, dialog closes (verify the use case was called with `ExplicitRule(mapping=...)` reflecting the table state — table is always source of truth at commit).
- Happy path (Covers R2, R3): "Auto: base stem" selected, table populates with 1 unambiguous binding, Accept enabled, commit succeeds.
- Edge case (Covers R3): Auto rule produces 1 ambiguous match. Table shows the ambiguous row with combobox of candidate channel names. Accept disabled until user picks. Once picked, Accept enabled.
- Edge case (Covers R3): "Manual mapping" selected, table starts empty. User assigns 2 of 4 .bin files. Other 2 stay "(unmapped)". Accept disabled. User clears one assignment. Still disabled.
- Error path (Covers R5): use case returns AppendReport with one error in `errors`. Dialog stays open with red-row highlight on the failed channel; success message lists the channels that did commit.
- Integration scenario (Covers R1, R2, R3, R5 — and AE3): open dialog against a real `.h5` fixture, walk through "Auto: zero-pad with offset", accept, verify `/decay/*` and `/provenance/decay/*` exist post-commit.

**Verification:**
- `pytest tests/test_gui_workflows/test_add_layer_tcspc.py` passes (using mock Protocols for the napari viewer per repo convention).
- Manual smoke test: with the user's actual TIFF + .bin dataset, run the dialog start-to-finish; verify the resulting `.h5` opens correctly in the launcher and that channels are bound as expected.

---

## System-Wide Impact

- **Interaction graph:** `AddLayerDialog` → `add_decay_to_dataset` use case → `DatasetStore.append_decay_layers` + `match_bin_to_intensity` + `assembler.assemble_tiles`. The initial-import path in `adapters/importer.py` also gains a call into `match_bin_to_intensity` (replacing the inline block); no other downstream code changes shape.
- **Error propagation:** Use case never raises on per-channel errors — collects into `AppendReport.errors`. Store raises `LayerAlreadyExists` for duplicate channels (use case catches and converts to a row in `AppendReport.errors`). Worker emits Qt error signal → dialog shows. No errors silently swallowed.
- **State lifecycle risks:** Append is **best-effort per-channel** — each channel's decay write + provenance write happen under one open `h5py.File(..., "a")` handle, then `f.flush()` + `os.fsync(f.id.get_vfd_handle()[0])` runs at end-of-channel before closing. This makes Python-process-crash recovery clean (prior channels are intact); it does NOT make HDF5 power-loss-safe — HDF5's superblock and B-tree updates are not journaled, and a power-loss event mid-flush can leave the file in a state where the structure is present but data extents are zero-filled. Upgrade to true durability would require SWMR mode + explicit transaction discipline, deferred to a future thread.
- **Concurrent-reader safety:** If the napari viewer has the `.h5` open in read mode while the dialog appends, behavior depends on whether the file was created with `libver='latest'` and SWMR-compatible options. Today's `DatasetStore.create()` does NOT set SWMR — so this thread documents a precondition: **the dialog refuses to commit when the viewer has the dataset open** (checked via the existing workflow-lock primitive that multi-select uses; alternatively, the dialog forces a viewer reload before committing). Implementer chooses one of these two paths in U4.
- **API surface parity:** `match_bin_to_intensity` is the only matcher; both initial-import and append call it. If a future thread (e.g., `.sdt` import) needs cross-format matching, it imports the same function. This is principle 5 expressed structurally.
- **Integration coverage:** Real-fixture round-trip in U3 and U4 verifies the full chain. Mocks alone won't catch the FlimConfig-flow-through-dialog gap that `flim_params` already exposed in `compress_dialog`.
- **Unchanged invariants:** The existing initial-import path's behavior on the user's mixed-convention fixture must produce byte-identical `.h5` group structure pre- and post-refactor (snapshot test in U1). `_update_label_display` and the napari viewer integration are untouched.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Refactor in U1 silently changes the initial-import path's matching behavior | U1 includes a snapshot integration test on the user's existing fixture; pre/post diff must be empty |
| Streaming append write performance regresses vs. initial-import write | Reuse the same `write_array(is_decay=True)` chunk/compression path; add the slow-mark large-array test in U2 |
| `LayerAlreadyExists` semantics surprise users who run append twice | Surface a clear UI message in U4 and offer the `force` toggle as a follow-up if requested |
| Cross-format rule encoded as a `Union` of dataclasses is awkward to serialize for `/metadata/cross_format_rule` attrs | Pin the serialization shape in U1 (e.g., `{"type": "ZERO_PAD_OFFSET", "pad_width": 2, "offset": 1}`); document in `domain/io/models.py` docstring |
| User has 6 uncommitted I/O files mid-modification right now | U1's first move: read working tree + decide whether to base off uncommitted state or ask user to commit/stash before starting. Plan stays valid either way |
| Adding a 4th rule strategy later forces rewrites | Keep the matcher dispatch pattern simple (`isinstance(rule, ZeroPadOffsetRule)` chain) — adding a strategy adds one branch + one variant; the dialog adds one dropdown entry |

---

## Documentation / Operational Notes

- **No new dependencies.** No `requirements.txt` change.
- **Per-module CLAUDE.md updates:** `src/percell4/domain/io/CLAUDE.md` (if it exists; create if not) gets a one-liner listing `cross_format.py` as the matcher home. `src/percell4/gui/CLAUDE.md` updates the AddLayerDialog tab inventory to include "TCSPC (.bin)".
- **Compound learning to capture on completion:** The cross-format rule extraction + append boundary is itself worth a `docs/solutions/architecture-patterns/` entry — "Append-as-execute(plan with N>0 items)" makes principles 3+4+5 visible in code.
- **Matrix doc:** as the audit pass produces `docs/audits/io-principles-matrix.md`, this thread's PRs reference cells by anticipated coordinates (e.g., `importer.py × #4`, `store.py × #5, #6`). When the audit doc lands, the citations resolve cleanly. When this thread's last PR merges, the audit doc's affected rows transition from `VIOLATION` to `OK` in the same PR per origin R10.

---

## Reviewer Findings — Deferred (handled in implementation or follow-up)

These items came from feasibility, design-lens, and adversarial reviewer passes after the plan was first drafted. They are non-blocking — the implementer resolves them in code review during U2-U4 — but they are spelled out here so nothing is silently lost.

### Dialog UX details (U4, mostly)

- **`ZeroPadOffsetRule` parameter exposure.** When the user picks "Auto: zero-pad with offset", surface `pad_width` and `offset` as inline `QSpinBox` widgets beneath the dropdown (defaults `pad_width=2, offset=1`). Hidden when other rules are selected. Tooltip: "TIFF pad width (zeros) and offset (bin tokens start at this number)."
- **Auto→Manual edit transition.** When the user picks "Auto: ..." then edits a cell, the dropdown stays at "Auto: ..." and a small "(modified)" suffix appears next to it. Re-picking "Auto: ..." clears all manual edits and re-runs the matcher (with confirmation if the manual edits weren't empty).
- **"Manual mapping" preserves Auto results.** Switching from Auto to Manual mapping keeps the current table state as the editable starting point — does NOT reset rows to "(unmapped)".
- **Dataset path confirmation.** Top of the tab shows "Appending to: `<dataset_name>.h5`" so the user sees which file is the destructive write target.
- **More channels than `.bin` files.** When `len(intensity_channels) > len(bin_files)` and the matcher succeeds for the bin files present, surface a banner: "N intensity channels have no matching .bin: ch0X, ch0Y. Append will leave these without decay." The user explicitly confirms or aborts.
- **Empty-files placeholder.** Empty table shows "No `.bin` files found in this directory." (theme.TEXT_DIM).
- **Ambiguous row visual.** Use `theme.WARNING` background for ambiguous rows and `theme.ERROR` background for rows where the user has selected "(unmapped)" as the binding. Already-conflicting rows (decay exists) use `theme.ACCENT` (Replace checkbox visible).
- **Scanning-in-progress state.** If the directory scan + match takes >200ms, show a progress spinner over the table area (existing `Worker` pattern, but for the discovery pass — not just the commit).
- **Post-commit messages.** Success: status bar shows "Appended N decay channels to `<dataset>`". Partial success: dialog stays open with red-row highlight on failed channels and message "Appended N of M channels. M-N failed (see rows)."
- **Re-run after partial success.** After partial success, the user can clear successful rows with a "Drop committed rows" button (handles the `LayerAlreadyExists` cascade that would otherwise occur on re-submit).
- **FlimConfig collapsibility.** Wrap the FLIM parameter form in a collapsed-by-default `QGroupBox` with checkbox header titled "Advanced: FLIM Parameters" — matches the Discover-TIFFs tab convention. Defaults from `FlimConfig` dataclass cover the user's microscope.

### Provenance and persistence details (U2)

- **`require_group(...).attrs[...]` write idiom.** `/provenance/decay/<channel_name>` is created via `f.require_group("provenance/decay/" + channel_name)` and the 6 fields land as attrs on that empty group. Empty groups with attrs are valid HDF5.
- **`match_evidence` length budget.** Cap the JSON-serialized evidence at 4 KB. If a future cascade rule produces longer evidence, write to `/provenance/decay/<ch>/match_evidence` as a small `H5T_VARIABLE` string dataset instead of an attr. Document the truncation rule in `cross_format.py`.
- **`content_sha256` semantics.** Hash is over the source `.bin` file bytes, computed once before the streaming read. It identifies the source file, not the binding — same hash + different rule = legitimate re-import case. Document in U2 docstring.
- **Provenance overwrite is silent.** `force=True` overwrites the prior provenance record with no audit trail. Acceptable for v1; if rerun-history matters in a later thread, version the record.

### Cross-cutting (U1, U3)

- **Importer-vs-append equivalence test.** U3's integration tests include one fixture that runs (a) initial-import path (TIFF + .bin together) and (b) initial-empty-h5 + append path (TIFF first, .bin appended via the new use case). Asserts identical `/decay/*` data, `/metadata/cross_format_rule`, and `/provenance/decay/*` modulo timestamps. This is the concrete test for "Batch ≡ Incremental."
- **Tile-token normalization parity.** If `.bin` files lack tile tokens, both the importer path and append path fall back to single-tile-per-channel. Verify against the user's actual fixture during U3.
- **`U2` per-channel atomicity test.** Update U2's "very large decay" test to ALSO verify that a simulated mid-channel exception leaves the file's structure consistent (prior channels intact, errored channel's `/decay/<name>` group either fully present or absent — never half-written). Use `monkeypatch` on `write_array` to inject the failure.
- **`Worker` callback bridge.** U4's worker setup explicitly constructs `progress_cb = lambda msg: worker.progress.emit(msg)` and passes it to `add_decay_to_dataset(..., progress_callback=progress_cb)`. The use case calls `progress_callback("Reading ch00...")` etc. at known points.
- **`StateChange.layers` follow-up.** Adding a granular `layers` flag to `StateChange` is a separate small refactor; this thread uses `Event.DATASET_CHANGED`. Tracked as a follow-up if heavier reload becomes a UX issue.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md](../brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md)
- **Source ideation:** [docs/ideation/2026-04-29-io-principles-ideation.md](../ideation/2026-04-29-io-principles-ideation.md) — the 7 principles
- **Related learnings:**
  - [docs/solutions/logic-errors/batch-compress-development-lessons.md](../solutions/logic-errors/batch-compress-development-lessons.md) — Layer Write Dispatch pattern
  - [docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md](../solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md) — discovery-scopes-processing-consumes
  - [docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md](../solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md) — `.bin` dtype, streaming, tile indices
  - [docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md](../solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md) — payload-type-per-group rule
- **Code anchors:**
  - `src/percell4/adapters/importer.py` (lines 187-440 — current cross-format match site, refactor source)
  - `src/percell4/store.py` (`write_array`, `write_labels`, `write_mask` — dispatch pattern to extend)
  - `src/percell4/domain/io/models.py` (`TokenConfig`, `TileConfig`, `FlimConfig` — frozen dataclass conventions)
  - `src/percell4/gui/add_layer_dialog.py` (existing tab structure to extend)
- **Related plans:**
  - [docs/plans/2026-04-17-refactor-eliminate-shims-and-temp-fixes-plan.md](2026-04-17-refactor-eliminate-shims-and-temp-fixes-plan.md) — precedent for phase-as-commit, dependency-ordered refactors
