---
title: Phasor plot goes deaf to session state after close→reopen
date: 2026-06-18
category: docs/solutions/ui-bugs/
module: interfaces/gui/peer_views/phasor_plot
problem_type: ui_bug
component: frontend_stimulus  # poor fit — Qt/Python GUI; closest view-layer bucket. Real component is a Qt QMainWindow peer-view subscribing to the Session event hub.
severity: high
symptoms:
  - "Phasor plot stops auto-updating after the window is closed then reopened (deaf to FILTER_CHANGED, ACTIVE_MASK_CHANGED, ACTIVE_CHANNEL_CHANGED, DATASET_CHANGED, ACTIVE_BIN_CHANGED)."
  - "Only in-window actions (mask checkbox toggle, recompute) repaint the plot; external session changes do nothing."
  - "Cell-selection filter silently ignored on cached auto-load until a manual recompute."
root_cause: logic_error
resolution_type: code_fix
related_components:
  - application/session
  - interfaces/gui/main_window
  - domain/flim/phasor_display
tags:
  - phasor
  - qt-lifecycle
  - event-subscription
  - closeevent-showevent
  - peer-view
  - session-hub
  - stale-ui
  - segmentation-labels
---

# Phasor plot goes deaf to session state after close→reopen

> **Component note:** the `component` enum has no Qt/Python-GUI value; `frontend_stimulus`
> is used as the nearest "view-layer interactive component" bucket. The real component is a
> Qt `QMainWindow` peer view (`PhasorPlotWindow`) subscribing to the shared `Session` event hub.

## Problem
`PhasorPlotWindow` is a standalone peer view that subscribes to the shared `Session`
observer hub so it repaints when cross-window state changes — filter-to-selection,
active mask, active channel, view bin. It stopped auto-updating on those changes; the
histogram only repainted on *in-window* actions (toggling a checkbox, clicking Compute
Phasor). Two independent root causes, both in
`src/percell4/interfaces/gui/peer_views/phasor_plot.py`.

## Symptoms
- Change the cell filter (Analysis → Filter to Selection) in another window → phasor pixel count does not change.
- Toggle the active mask elsewhere → "Filter by active mask" checkbox does not resync.
- The plot *does* update when you toggle one of its own checkboxes or recompute — which masks the bug during casual testing and produces a confusing "one step behind" feel.
- After any close→reopen of the window: completely deaf to all session events.
- Even on a first open (no close yet), the cell-selection filter was a no-op against an auto-loaded cached phasor until Compute Phasor was clicked.

## What Didn't Work
The window's `closeEvent` does `self.hide()` + `event.ignore()` — it is **hidden, never
destroyed** (the hide-not-destroy pattern, used for geometry persistence). But `closeEvent`
also tore down every Session subscription:

```python
for unsub in getattr(self, '_unsubs', []):
    try:
        unsub()
    except ValueError:
        pass
```

Subscriptions were only ever created once, inline in `__init__`. `showEvent` re-rendered
preview layers and auto-loaded cached data but **never re-subscribed**. So the lifecycle
was: construct (subscribed) → close (unsubscribed) → show (still unsubscribed) →
permanently deaf.

This had been latent since the architecture was first wired (session history):
- The **April 16** Session-hub migration (`1a74f4fe`) established subscribe-in-`__init__` / unsubscribe-in-`closeEvent` alongside the already-present hide-not-destroy pattern; the design *assumed* `showEvent` re-firing on reopen was enough — but `showEvent` never re-subscribed. (session history)
- **April 17** (`224428d3`) added the `try/except ValueError: pass` guard around `unsub()` to stop a double-unsubscribe crash — a symptom patch that was itself evidence the subscription lifecycle was already fragile. (session history)
- A **May 4** code-review pass (`df12c0b4`) found *other* lifecycle gaps (`_cleared_mask` surviving `_g_map = None` resets) but looked at what `set_phasor_data` didn't reset, never at whether the subscription callbacks were still reachable. (session history)

The naive "just don't unsubscribe on close" is the wrong fix: the `Session` hub stores raw
callback lists with no dedup (`self._observers[event].append(cb)`), so leaving subscriptions
in place and also re-subscribing would double-fire every handler. The correct shape is
teardown-on-hide **plus** an idempotent rebuild-on-show.

## Solution

### Fix 1 (primary) — idempotent re-subscription across the hide/show cycle
Extract the inline subscription block into a guarded helper, called from **both**
`__init__` and `showEvent`; `closeEvent` clears the list so the guard lets `showEvent`
rebuild.

`__init__` delegates to the helper:

```python
self._unsubs: list[Callable[[], None]] = []
self._subscribe_session()
```

```python
def _subscribe_session(self) -> None:
    """(Re)establish Session subscriptions. Idempotent.

    Called from ``__init__`` and ``showEvent``. ``closeEvent`` clears
    ``_unsubs`` after unsubscribing, so a hidden→reshown window rebinds
    its handlers here. A no-op when already subscribed.
    """
    if self._unsubs:
        return
    self._unsubs = [
        self._session.subscribe(Event.FILTER_CHANGED, self._on_filter_changed),
        self._session.subscribe(Event.ACTIVE_MASK_CHANGED, self._on_active_mask_changed),
        self._session.subscribe(Event.DATASET_CHANGED, self._on_dataset_changed),
        self._session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, self._on_active_channel_changed),
        self._session.subscribe(Event.ACTIVE_BIN_CHANGED, self._on_active_bin_changed),
    ]
```

`closeEvent` clears the list after unsubscribing (the guard keys off a non-empty list):

```python
for unsub in getattr(self, '_unsubs', []):
    try:
        unsub()
    except ValueError:
        pass  # already unsubscribed
self._unsubs = []   # so showEvent's _subscribe_session rebinds rather than skipping
```

`showEvent` re-subscribes and, when data is already loaded, resyncs state that may have
changed while hidden+deaf (subscriptions only deliver *future* events):

```python
def showEvent(self, event) -> None:
    super().showEvent(event)
    self._subscribe_session()   # rebind subscriptions torn down by last closeEvent
    if self._g_map is None:
        self._try_auto_load_cached()
    else:
        # Events may have fired while hidden+unsubscribed; resync the mask
        # checkbox and repaint against current session filter state.
        self._on_active_mask_changed()
        self._filter_timer.start()
    if self._roi_widgets and self._g_map is not None:
        self._preview_timer.start()
```

### Fix 2 (secondary) — auto-load must supply labels or the cell filter is a silent no-op
The cell-selection filter in `domain/flim/phasor_display.py` is gated on labels being present:

```python
if filter_ids is not None and labels_flat is not None:   # phasor_display.py:79
    cell_mask = np.isin(labels_flat, list(filter_ids))
    valid = valid & cell_mask
```

`_try_auto_load_cached` passed `labels=None` — a deliberate May-3 tradeoff
(commit `3c201b25`: *"labels=None — cell filter degraded; user re-clicks Compute to
re-engage"*) because the cell-segmentation labels require a separate HDF5 read not part of
the cached phasor. The cost: the cell filter did nothing on auto-loaded data until a
recompute. (session history)

Fix: add a `get_seg_labels` provider (wired in `main_window.py` to `_get_active_seg_labels`)
and pull shape-matched labels on auto-load.

```python
# was: labels=None  # cell filter degraded; user re-clicks Compute to re-engage
labels = self._seg_labels_matching(cached.g_map)
self.set_phasor_data(cached.g_map, cached.s_map, intensity=cached.intensity, labels=labels)
```

```python
def _seg_labels_matching(self, g_map: np.ndarray) -> np.ndarray | None:
    """Return active segmentation labels iff they align with ``g_map``.

    None when no provider is wired, the provider yields nothing, or the
    labels' shape mismatches (e.g. a binning / timepoint mismatch). A None
    result degrades the cell-selection filter exactly as before.
    """
    if self._get_seg_labels is None:
        return None
    try:
        labels = self._get_seg_labels()
    except Exception:  # a label-read failure must not break auto-load
        return None
    if labels is None:
        return None
    labels = np.asarray(labels)
    if labels.shape != g_map.shape:
        return None
    return labels
```

```python
# main_window.py factory
"phasor_plot": lambda: PhasorPlotWindow(
    session,
    get_repo=lambda: self._repo,
    get_seg_labels=self._get_active_seg_labels,
),
```

## Why This Works
- **Symmetric lifecycle.** A hide-not-destroy window has a long-lived Python object but a flapping *visible* lifetime. Subscriptions are a visible-lifetime resource: tear down on `closeEvent`, rebuild on `showEvent`, both routed through one idempotent helper.
- **Idempotency guards against double-fire.** The `Session` hub does not dedup callbacks, so the `if self._unsubs: return` guard makes `_subscribe_session()` safe to call from `__init__` *and* every `showEvent` without stacking duplicate handlers.
- **Resync-on-show covers missed events.** Subscriptions deliver only future events, so `showEvent` pulls current session truth (`_on_active_mask_changed()` + `_filter_timer.start()`) when data is already loaded.
- **Shape-gating preserves safe degradation.** Feeding misaligned labels (bin/timepoint mismatch) into `np.isin` against a different-sized `g_map` would corrupt the filter; returning `None` reproduces the exact pre-fix behavior on the rare path while engaging the filter live on the common path.

## Prevention
Regression tests in `tests/test_gui_workflows/test_phasor_subscription_lifecycle.py`. The
load-bearing assertion is not "is `_unsubs` non-empty" but "does a real `session.set_filter`
after a close→reopen change the rendered pixel count":

```python
def test_filter_still_refreshes_after_close_reopen(qtbot, session_with_dataset):
    win = PhasorPlotWindow(session_with_dataset)
    qtbot.addWidget(win)
    g, s, labels = _maps_with_labels()      # two labels, 32 px each, 64 total
    win.set_phasor_data(g, s, labels=labels)

    win.close()   # closeEvent unsubscribes + clears _unsubs
    win.show()    # showEvent must re-subscribe
    qtbot.wait(50)
    assert win._unsubs, "subscriptions were not re-established on show"

    session_with_dataset.set_filter(frozenset({1}))
    qtbot.wait(300)   # debounce _filter_timer (150 ms)
    assert "32" in win._status.currentMessage()   # pixel count dropped 64 -> 32
```

Companion tests assert resync of a filter set *while hidden*
(`test_reopen_resyncs_filter_set_while_hidden`), that auto-load populates `_labels_flat` so
the cell filter engages live (`test_auto_load_populates_labels_so_filter_works`), and that a
shape mismatch falls back to `None`.

General guardrails:
1. **Any hide-not-destroy window (`event.ignore()` + `hide()`) that unsubscribes from a shared hub on close MUST re-subscribe on show.** Treat subscriptions as a visible-lifetime resource with symmetric setup/teardown through one idempotent helper called from both `__init__` and `showEvent`.
2. **When the observer hub does not dedup callbacks, the re-subscribe path must be idempotent** (guard on already-subscribed) to avoid double-firing.
3. **`showEvent` must reconcile state that changed while hidden** — pull current session truth on show when data is already loaded.
4. **A filter gated on optional inputs being present** (here `labels_flat is not None`) is silently disabled whenever a load path forgets to supply them. Degrade only on a verifiable condition (shape mismatch), never as a default-because-it's-easier.

## Related Issues
- `docs/solutions/ui-bugs/phasor-auto-load-skipped-on-dataset-switch-2026-05-06.md` — same window and auto-load/subscription surface; **now partially stale**: it describes Session subscriptions as bound once for the window's lifetime, which this fix replaces with the close-teardown / show-rebind lifecycle. Drifted line numbers too. Refresh candidate.
- `docs/solutions/ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md` — same class of silent-filter-no-op (filters not threaded into the pixel pipeline); this fix is consistent with its "thread the filter through" guidance.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` — stale peer-view state on dataset switch; same refresh chain via `set_phasor_data` invalidation.
- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md` — same file; `ACTIVE_BIN_CHANGED` invalidation/reload chain.
- `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md` — adjacent peer-view ↔ session subscription/ownership pattern.

Fix commit: `2ed31a18` (`fix(phasor): keep plot live with session state across close/reopen`).
