"""Adaptive Local Clipping per-frame stacking (U8)."""

from __future__ import annotations

import numpy as np

import percell4.gui.adaptive_clip_panel as acp


def test_run_adaptive_detection_stack_loops_and_stacks(monkeypatch):
    """The stack worker runs detection per frame and stacks to (T,H,W); each
    frame's mask is that frame's own detection (not frame 0 broadcast), and the
    auto window is computed per frame (contract D3)."""

    def fake_detect(frame, sigma, settings, auto_window):
        # Encode the frame's mean in the window so per-frame computation shows.
        w = int(frame.mean())
        m = (frame > frame.mean()).astype(np.uint8)
        return m, w

    monkeypatch.setattr(acp, "run_adaptive_detection", fake_detect)

    image = np.stack(
        [
            np.full((4, 4), 1, dtype=np.float32),
            np.full((4, 4), 10, dtype=np.float32),
        ],
        axis=0,
    )
    image[1, 0, 0] = 100  # frame 1 has a bright pixel -> non-trivial mask

    mask, windows = acp.run_adaptive_detection_stack(image, 0.0, object(), True)

    assert mask.shape == (2, 4, 4)
    assert mask.dtype == np.uint8
    # Per-frame windows differ -> the window was computed per frame, not once.
    assert windows == [1, 15]
    assert windows[0] != windows[1]
    # Frame 1's mask flags only its bright pixel; frame 0 is uniform -> all zero.
    assert mask[0].sum() == 0
    assert mask[1, 0, 0] == 1


def test_accept_puncta_mask_persists_thw(tmp_h5):
    """AcceptPunctaMask is shape-transparent: a (T,H,W) mask round-trips (so the
    panel only needs to hand it a per-frame stack)."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.application.session import Session
    from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((3, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    repo = Hdf5DatasetRepository()
    handle = repo.open(tmp_h5)
    session = Session()
    session.set_dataset(handle)

    mask = np.zeros((3, 8, 8), dtype=np.uint8)
    mask[1, 2, 2] = 1
    res = AcceptPunctaMask(repo, session).execute(mask, "adaptive")

    assert res.n_positive == 1
    assert store.read_mask("adaptive").shape == (3, 8, 8)
    assert store.read_mask("adaptive", timepoint=1)[2, 2] == 1
