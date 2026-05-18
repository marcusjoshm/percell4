---
title: View bin parameter accepted by use cases but not forwarded by GUI callers
date: 2026-05-18
category: integration-issues
module: src/percell4/interfaces/gui/task_panels/flim_panel.py, src/percell4/interfaces/gui/peer_views/phasor_plot.py, src/percell4/application/use_cases/load_cached_phasor.py
problem_type: integration_issue
component: tooling
symptoms:
  - "Toggling the SessionWindow view-bin QSpinBox re-binned napari image layers but the phasor plot did not update"
  - "Derived data (phasor, lifetime, wavelet, GMM) rendered at native resolution regardless of active view bin"
  - "PhasorPlotWindow cache invalidation on Event.ACTIVE_BIN_CHANGED fired, yet reloads returned unchanged native-shape arrays"
  - "Unit tests for use-case kwarg acceptance and cache invalidation both passed; the toggle -> invalidate -> reload -> display chain was broken end-to-end"
root_cause: incomplete_setup
resolution_type: code_fix
severity: high
tags:
  - phasor
  - view-bin
  - spatial-binning
  - use-case-wiring
  - gui-integration
  - flim
  - session-active-bin
  - cache-invalidation
related_components:
  - assistant
  - documentation
---

# View bin parameter accepted by use cases but not forwarded by GUI callers

## Problem

After a 15-unit feature (U0–U14) added dataset-wide spatial binning, toggling
the SessionWindow view-bin QSpinBox re-binned napari image layers correctly
but the phasor plot did not update — it kept rendering native-shape `(g, s)`
arrays. The receiver (`LoadCachedPhasor` and four other FLIM use cases) had
been wired to accept a `view_bin` kwarg, and the cache invalidation on
`Event.ACTIVE_BIN_CHANGED` correctly cleared 11 ndarray caches, but every
GUI caller forgot to pass `session.active_bin`, so each post-invalidation
reload defaulted to `view_bin=1` and re-hydrated from the same native-shape
arrays.

## Symptoms

- User toggles the view-bin SpinBox from k=1 → k=2 → k=3 in SessionWindow.
- Napari image layers (intensity, segmentation, masks) re-render at the
  binned shape — correct.
- The phasor plot's histogram and ROIs do not change shape at all — looks
  identical regardless of bin.
- No error, no console warning. The 11 ndarray caches were dutifully
  cleared on `ACTIVE_BIN_CHANGED` and dutifully refilled with the same
  content from disk.
- Affects: phasor histogram on cache reload, freshly computed
  phasor/wavelet/lifetime, GMM input arrays.

## What Didn't Work

U14 made two changes that each looked sufficient in isolation but together
still produced unchanged output:

1. **Receiver-side kwarg.** `ComputePhasor`, `ApplyWavelet`,
   `ComputeLifetime`, `RunPhasorGMM` were each given `view_bin: int = 1`.
   The kwarg existed at the use-case boundary; no caller passed it. **A
   receiver-side parameter with no sender wiring is dead code that lies
   about being live.**

2. **Cache invalidation on `ACTIVE_BIN_CHANGED`.**
   `PhasorPlotWindow._invalidate_for_bin_change` enumerated and cleared
   11 ndarray caches (`_g_map`, `_g_map_unfiltered`, `_s_map`,
   `_s_map_unfiltered`, `_intensity`, `_labels`, `_labels_flat`,
   `_active_mask_array`, `_active_mask_flat`, `_cleared_mask`, per-ROI
   `cached_mask`). The invalidation fired correctly. But the immediate
   re-hydration path — `LoadCachedPhasor.execute` triggered by
   `_try_auto_load_cached` — had never been given a `view_bin` parameter
   at all. Every read defaulted to k=1 and refilled the just-cleared
   caches with the same native-shape arrays.

3. **Existing tests.** U14 verified separately that (a) the four use
   cases accepted `view_bin` and (b) the PhasorPlot invalidation
   chokepoint cleared the right caches. Both passed. Nothing exercised
   the full chain: bin toggle → invalidate → reload → display. The
   integration gap had zero coverage.

## Solution

Close the wiring at every caller and add the missing kwarg to the read
path.

**1. `LoadCachedPhasor.execute` — add `view_bin` to the read path itself.**

Before (`src/percell4/application/use_cases/load_cached_phasor.py`):

```python
def execute(self, channel: str) -> CachedPhasorResult:
    handle = self._session.dataset
    if handle is None:
        raise NoDatasetError("No dataset loaded")
    g_map = self._repo.read_array(handle, f"phasor/{channel}/g")
    s_map = self._repo.read_array(handle, f"phasor/{channel}/s")
    # ... g_filtered, s_filtered, decay reads, all without view_bin
```

After:

```python
def execute(self, channel: str, view_bin: int = 1) -> CachedPhasorResult:
    handle = self._session.dataset
    if handle is None:
        raise NoDatasetError("No dataset loaded")
    g_map = self._repo.read_array(
        handle, f"phasor/{channel}/g", view_bin=view_bin
    )
    s_map = self._repo.read_array(
        handle, f"phasor/{channel}/s", view_bin=view_bin
    )
    # ... all 5 reads (g, s, g_filtered, s_filtered, decay/<ch>) forward view_bin
```

The store dispatches per path: `mean_bin_2d` for intensive `g/s`
(magnitudes preserved at any k), `sum_bin_decay` for the decay tensor
(T axis preserved).

**2. Representative GUI caller — `_on_compute_phasor` in
`src/percell4/interfaces/gui/task_panels/flim_panel.py:289`.**

Before:

```python
repo = self._get_repo()
cached = LoadCachedPhasor(repo, self.data_model.session).execute(
    active_channel,
)
```

After:

```python
repo = self._get_repo()
# Session view bin propagates through the cache load so cached g/s
# and decay-derived intensity arrive at the binned shape the phasor
# plot expects to display.
active_bin = self.data_model.session.active_bin
cached = LoadCachedPhasor(repo, self.data_model.session).execute(
    active_channel, view_bin=active_bin,
)
```

Same pattern applied at 8 caller-sites total: `flim_panel.py` lines 289
(`LoadCachedPhasor` in compute_phasor handler), 326 (`ComputePhasor.execute`),
349 (decay read for histogram weights), 396 (`LoadCachedPhasor` in
apply_wavelet handler), 435 (`ApplyWavelet.execute`), 505
(`ComputeLifetime.execute`), 681 (`RunPhasorGMM` via Worker kwargs); plus
`phasor_plot.py:2142` (`LoadCachedPhasor` in `_try_auto_load_cached`).

**3. Capture-at-construction for the GMM worker
(`flim_panel.py:660-704`).**

The GMM runs on a `QThread` worker. If the user toggles the bin
mid-flight, naive `session.active_bin` reads inside the worker would
resolve to a different bin than the one current when GMM started — the
result would not match the user's pre-click intent. Capture in the
kwargs dict before constructing the worker:

```python
kwargs = dict(
    channel=active_channel,
    # ... other params ...
    # Capture session.active_bin at worker-construction (race-safe
    # per the U12 capture pattern) so a mid-flight bin toggle cannot
    # alter the resolution of an in-flight GMM result.
    view_bin=self.data_model.session.active_bin,
)
# ...
self._gmm_worker = Worker(uc.execute, **kwargs)
self._gmm_worker.start()
```

The capture happens on the GUI thread before `Worker(...)` exists; the
worker thread reads its kwarg snapshot, not live session state.

**4. Regression tests in
`tests/test_application/test_load_cached_phasor.py`.** A recording fake
repo captures `(path, view_bin)` per call:

```python
class _RecordingRepo:
    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.reads: list[tuple[str, int]] = []

    def read_array(self, handle, path, view_bin=1):
        self.reads.append((path, view_bin))
        if path not in self.arrays:
            raise KeyError(path)
        return self.arrays[path]


def test_load_cached_phasor_forwards_view_bin_to_every_read(session):
    repo = _RecordingRepo()
    repo.arrays["phasor/ch0/g"] = np.zeros((8, 8), dtype=np.float32)
    # ... s, g_filtered, s_filtered, decay/ch0 ...

    LoadCachedPhasor(repo, session).execute("ch0", view_bin=3)

    for path, vb in repo.reads:
        assert vb == 3, f"read of {path} used view_bin={vb}, expected 3"


def test_load_cached_phasor_default_view_bin_is_one(session):
    # Backward-compat: omitting view_bin reads at k=1.
    LoadCachedPhasor(repo, session).execute("ch0")
    assert all(vb == 1 for _, vb in repo.reads)
```

Pre-fix the first test fails at the use-case signature (the `view_bin`
kwarg did not exist). Post-fix it pins both that every read forwards
`view_bin` and that the default is `1`.

## Why This Works

The root cause is a **wiring-not-logic** failure: the receiver knew how
to bin, the invalidator knew when to clear, but nothing ever told the
read path which bin to use. Once `LoadCachedPhasor.execute` accepts
`view_bin` and forwards it to its five `repo.read_array(...)` calls,
and once every caller threads `session.active_bin` into its use-case
invocation, the chain reconnects:

1. User toggles the SpinBox → `Session` emits `ACTIVE_BIN_CHANGED`.
2. `PhasorPlotWindow._invalidate_for_bin_change` clears the 11 ndarray
   caches.
3. `_try_auto_load_cached` calls
   `LoadCachedPhasor(..., view_bin=session.active_bin)`.
4. The store dispatches each read through its per-rule downsampler
   (`mean_bin_2d` for `g/s`, `sum_bin_decay` for `decay/<ch>`).
5. The phasor plot renders the binned arrays.

Pre-fix, step 3 silently dropped the bin and step 4 returned native-shape
arrays; the cache was cleared and refilled with the same content.

The GMM worker fix is a slightly different concern — it is about *when*
the bin is captured, not *whether*. Reading `session.active_bin` inside
a worker thread is a race because the GUI thread can mutate session
state during the worker's run. Capturing into the kwargs dict on the
GUI thread before `Worker(...)` is constructed gives the worker an
immutable snapshot of "the bin the user intended when they clicked Run."

## Prevention

**Pattern: optional kwarg + every caller.** When you add an optional
kwarg to a use case (or any function called from many sites), do not
stop at the signature. Immediately:

1. Grep for every call site:
   `rg 'LoadCachedPhasor\(.*\)\.execute\('` or the moral equivalent.
2. For each site, identify which context owns the value (here:
   `session.active_bin`).
3. Pass it explicitly. Defaulting silently to "the safe value" is the
   failure mode — the safe default is what hid the bug for an entire
   15-unit plan.

A receiver-side kwarg with no sender wiring is dead code that lies about
being live.

**Test pattern: walk the full chain end-to-end.** U14's tests verified
two ends of the chain in isolation (use case accepts kwarg, cache clears
on event). Neither caught the broken middle. For state-propagation
features, write at least one integration test that exercises:

```
state change -> invalidation -> reload -> observable display contents
```

For this codebase that means: emit the session event, assert the caches
cleared, then assert the reload populated them with binned-shape arrays
(not just any arrays).

**Recording-mock pattern.** A fake repo that records `(path, kwarg, ...)`
per call is the cheapest way to verify wiring at the use-case layer
without spinning up Qt:

```python
class _RecordingRepo:
    def __init__(self):
        self.reads: list[tuple[str, int]] = []

    def read_array(self, handle, path, view_bin=1):
        self.reads.append((path, view_bin))
        return self.arrays[path]
```

You do not need to assert on array contents — just assert that the
wiring delivered the parameter to every read. Cheap, fast, catches the
exact class of bug U14 had.

**Capture-at-construction for worker race safety.** Whenever a
`Worker(...)` wraps a use case that reads from `Session`, capture every
session-derived value into the kwargs dict on the GUI thread **before**
the worker is constructed. Never read `session.foo` inside the worker.
This is the U12 pattern (Cellpose) and now the same pattern for the GMM
worker — make it a rule across the codebase. A mid-flight toggle must
not be able to retroactively change the meaning of an in-flight result.

## Related Issues

- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
  — Closest parent. The 5-vector compound enumerated stacked cache layers
  that must be invalidated together when in-session state changes. This
  bug is a **sixth class** (or an extension of Vector 4): invalidation
  is necessary but not sufficient when the **read path that immediately
  follows** has not been parameterized on the new dimension. Worth
  amending that doc with a note that read paths consuming a worker-input
  dimension must be plumbed alongside the invalidation that triggers
  their re-execution.

- `docs/solutions/ui-bugs/phasor-auto-load-skipped-on-dataset-switch-2026-05-06.md`
  — Sibling lifecycle bug in the same window. There, the invalidation
  ran but the auto-load handler was not re-invoked. Here, the auto-load
  handler ran but did not forward the new state. Both prescribe a
  per-event audit grep — that doc's "every `subscribe(...)` must also
  fire from `_on_dataset_changed`" rule is the cousin of this doc's
  "every caller must forward shape-relevant Session fields."

- `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`
  — Same "N call sites drift when pipeline gains a new dimension"
  pattern, on a different axis. There the prescription was to centralize
  into one `_compute_visible_valid_2d()` helper and pin with a
  structural-equality regression test. Same prescription applies here:
  if eight GUI handlers each forward kwargs to the FLIM use cases,
  consider extracting a shared kwarg-builder helper or pinning the
  forwarding with the recording-mock pattern above.

- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
  — Same files, same pipeline. The "derive at one site, do not let
  consumers reimplement the chain" lesson generalizes to "forward at one
  helper, do not let callers omit the kwarg silently."

- `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
  — The 5-step bridge convention this bug followed correctly for the
  emit side (`Event.ACTIVE_BIN_CHANGED` flowed through `CellDataModel`
  to `state_changed`). The companion rule this bug surfaces: emit-side
  parity is necessary but not sufficient — the **consume side** (every
  caller of every worker that consumes a shape-relevant Session field)
  must be audited too.
