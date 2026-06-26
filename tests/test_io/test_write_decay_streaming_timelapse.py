"""U2 — write_decay_streaming places per-timepoint frames into a 4-D /decay.

The streamer allocates a ``(T_acq, H, W, T_bins)`` dataset on the first frame
and writes each subsequent frame in place at the leading ``[timepoint]`` index,
reusing the same geometry every frame. The ``n_acq == 1`` default stays
byte-identical to the legacy 3-D write (covered by the golden passthrough tests).
"""

from __future__ import annotations

import h5py
import numpy as np

from percell4.adapters.importer import write_decay_streaming
from percell4.store import DatasetStore

_H, _W, _TB, _NT = 6, 8, 4, 3


def _bin_dims() -> dict:
    return {"x_dim": _W, "y_dim": _H, "t_dim": _TB, "dtype": "uint16",
            "dim_order": "YXT", "header_bytes": 0}


def _tile(t: int) -> np.ndarray:
    y = np.arange(_H)[:, None, None]
    x = np.arange(_W)[None, :, None]
    k = np.arange(_TB)[None, None, :]
    return ((t + 1) * 1000 + y * 100 + x * 10 + k).astype(np.uint16)


def test_streaming_writes_4d_per_timepoint(tmp_path):
    store = DatasetStore(tmp_path / "tl.h5")
    store.create(metadata={"channel_names": ["ch00"],
                           "native_shape": (_H, _W), "n_timepoints": _NT})
    bins = {}
    for t in range(_NT):
        p = tmp_path / f"t{t}.bin"
        p.write_bytes(_tile(t).tobytes())
        bins[t] = p

    for t in range(_NT):
        write_decay_streaming(
            h5_path=str(store.path), channel_name="ch00",
            tile_bins={0: bins[t]}, bin_dims=_bin_dims(),
            tile_h=_H, tile_w=_W, n_bins=_TB, out_h=_H, out_w=_W,
            positions={0: (0, 0)}, use_tiling=False, n_acq=_NT, timepoint=t,
        )

    with h5py.File(store.path, "r") as f:
        ds = f["decay/ch00"]
        assert ds.shape == (_NT, _H, _W, _TB)
        assert list(ds.attrs["dims"]) == ["Tacq", "H", "W", "T"]
        assert ds.chunks == (1, min(64, _H), min(64, _W), _TB)
        for t in range(_NT):
            np.testing.assert_array_equal(ds[t], _tile(t).astype(np.float32))

    # The per-timepoint read chokepoint returns each frame as 3-D on disk.
    for t in range(_NT):
        np.testing.assert_array_equal(
            store.read_decay("ch00", timepoint=t), _tile(t).astype(np.float32)
        )


def test_first_frame_alloc_does_not_clobber_on_later_frames(tmp_path):
    """Writing frame 1 must not delete frame 0 (the delete is t==0-gated)."""
    store = DatasetStore(tmp_path / "tl.h5")
    store.create(metadata={"channel_names": ["ch00"],
                           "native_shape": (_H, _W), "n_timepoints": 2})
    for t in (0, 1):
        p = tmp_path / f"t{t}.bin"
        p.write_bytes(_tile(t).tobytes())
        write_decay_streaming(
            h5_path=str(store.path), channel_name="ch00",
            tile_bins={0: p}, bin_dims=_bin_dims(),
            tile_h=_H, tile_w=_W, n_bins=_TB, out_h=_H, out_w=_W,
            positions={0: (0, 0)}, use_tiling=False, n_acq=2, timepoint=t,
        )
    # Frame 0 survived the frame-1 write.
    np.testing.assert_array_equal(
        store.read_decay("ch00", timepoint=0), _tile(0).astype(np.float32)
    )
