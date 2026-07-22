"""Tests for RoundCard — one thresholding round as a self-contained card."""

from __future__ import annotations

from percell4.gui.workflows.single_cell.round_card import (
    METHOD_AUTO_EXTRACT,
    METHOD_GROUPED,
    RoundCard,
)


def _card(qtbot, index=0, channels=("mNG", "mCherry")) -> RoundCard:
    c = RoundCard(index, list(channels))
    qtbot.addWidget(c)
    return c


def test_default_is_grouped_otsu(qtbot):
    c = _card(qtbot)
    d = c.to_dict()
    assert d["method"] == METHOD_GROUPED
    assert d["algorithm"] == "gmm"
    assert d["gmm_max"] == 10
    assert d["kmeans_k"] == 3
    assert d["min_particle_size"] == 0.0
    assert d["min_particle_size_unit"] == "px"
    # The σ-clipping-only keys never appear.
    assert "k" not in d
    assert "global_sigma" not in d


def test_method_combo_offers_exactly_two_methods(qtbot):
    c = _card(qtbot)
    items = [c._method.itemText(i) for i in range(c._method.count())]
    assert items == [METHOD_GROUPED, METHOD_AUTO_EXTRACT]
    assert not any("σ-clipping" in i or "single-window" in i for i in items)


def test_method_switch_shows_and_hides_subgroups(qtbot):
    c = _card(qtbot)
    # Grouped Otsu (default): grouped box shown, ALC box hidden.
    assert c._grouped_box.isVisibleTo(c)
    assert not c._alc_box.isVisibleTo(c)
    # Switch to ALC: inverse.
    c._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert not c._grouped_box.isVisibleTo(c)
    assert c._alc_box.isVisibleTo(c)
    assert c.to_dict()["method"] == METHOD_AUTO_EXTRACT
    # Switch back: grouped values retained.
    c._gmm_max.setValue(7)
    c._method.setCurrentText(METHOD_GROUPED)
    assert c._grouped_box.isVisibleTo(c)
    assert c.to_dict()["gmm_max"] == 7


def test_alc_sigma_seeded_to_one_on_entry(qtbot):
    """Entering an ALC method from σ=0 seeds the validated 1.0 (a 0 presmooth
    collapses detection)."""
    c = _card(qtbot)
    assert c.to_dict()["sigma"] == 0.0  # grouped default
    c._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert c.to_dict()["sigma"] == 1.0


def test_alc_sigma_not_overwritten_if_already_set(qtbot):
    c = _card(qtbot)
    c._sigma.setValue(2.5)
    c._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert c.to_dict()["sigma"] == 2.5


def test_grouped_sigma_not_seeded(qtbot):
    c = _card(qtbot)
    c._method.setCurrentText(METHOD_AUTO_EXTRACT)
    c._method.setCurrentText(METHOD_GROUPED)
    # Leaving ALC does not force σ back to 0, but re-entering grouped never seeds.
    c._sigma.setValue(0.0)
    assert c.to_dict()["sigma"] == 0.0


def test_from_dict_does_not_reseed_sigma(qtbot):
    """A programmatic load (reorder round-trip) preserves a saved σ, even 0."""
    c = _card(qtbot)
    c.from_dict({"method": METHOD_AUTO_EXTRACT, "sigma": 0.0, "channel": "mNG"})
    assert c.to_dict()["sigma"] == 0.0
    assert c.to_dict()["method"] == METHOD_AUTO_EXTRACT


def test_from_dict_tolerates_stale_sigma_clipping_keys(qtbot):
    c = _card(qtbot)
    c.from_dict(
        {
            "method": METHOD_AUTO_EXTRACT,
            "channel": "mCherry",
            "k": 3.0,  # stale σ-clipping keys — must be ignored, not error
            "global_sigma": True,
        }
    )
    d = c.to_dict()
    assert "k" not in d and "global_sigma" not in d
    assert d["channel"] == "mCherry"


def test_round_trip_is_lossless(qtbot):
    c1 = _card(qtbot)
    c1._method.setCurrentText(METHOD_AUTO_EXTRACT)
    c1._name.setText("focus_round")
    c1._d_min.setValue(0.85)
    c1._size_unit.setCurrentText("px")
    c1._cnr_on.setChecked(True)
    c1._cnr_threshold.setValue(6.5)
    c1._min_size.setValue(12.0)
    c1._min_size_unit.setCurrentText("µm²")
    data = c1.to_dict()

    c2 = _card(qtbot)
    c2.from_dict(data)
    assert c2.to_dict() == data


def test_cnr_threshold_gated_by_split_and_forced(qtbot):
    c = _card(qtbot)
    c._method.setCurrentText(METHOD_AUTO_EXTRACT)
    # split off -> threshold disabled, forced disabled.
    assert not c._cnr_threshold.isEnabled()
    assert not c._cnr_forced.isEnabled()
    # split on -> threshold + forced live.
    c._cnr_on.setChecked(True)
    assert c._cnr_threshold.isEnabled()
    assert c._cnr_forced.isEnabled()
    # forced on -> threshold greyed (overridden).
    c._cnr_forced.setChecked(True)
    assert not c._cnr_threshold.isEnabled()
    # split off clears forced.
    c._cnr_on.setChecked(False)
    assert not c._cnr_forced.isChecked()


def test_algorithm_gates_gmm_vs_kmeans(qtbot):
    c = _card(qtbot)  # grouped by default
    c._algorithm.setCurrentText("gmm")
    assert c._gmm_max.isEnabled()
    assert not c._kmeans_k.isEnabled()
    c._algorithm.setCurrentText("kmeans")
    assert not c._gmm_max.isEnabled()
    assert c._kmeans_k.isEnabled()


def test_auto_extract_allows_zero_dmin(qtbot):
    c = _card(qtbot)
    c._method.setCurrentText(METHOD_AUTO_EXTRACT)
    c._d_min.setValue(0.0)  # auto-detect
    assert c.to_dict()["d_min_um"] == 0.0


def test_invalid_name_flagged_and_reported(qtbot):
    c = _card(qtbot)
    c._name.setText("1bad name")  # starts with digit + space
    assert not c.name_is_valid()
    assert "5b2a2a" in c._name.styleSheet()
    c._name.setText("good_name")
    assert c.name_is_valid()
    assert c._name.styleSheet() == ""


def test_set_channels_preserves_pick(qtbot):
    c = _card(qtbot, channels=("a", "b", "c"))
    c._channel.setCurrentText("b")
    c.set_channels(["a", "b", "c", "d"])
    assert c.to_dict()["channel"] == "b"


def test_set_channels_empty_disables(qtbot):
    c = _card(qtbot)
    c.set_channels([])
    assert not c._channel.isEnabled()


def test_set_index_updates_header_not_name(qtbot):
    c = _card(qtbot, index=0)
    c._name.setText("myround")
    c.set_index(2)
    assert "3 · myround" in c._header_label.text()
    assert c.to_dict()["name"] == "myround"


def test_move_and_remove_signals_fire(qtbot):
    c = _card(qtbot)
    moved_up = []
    moved_down = []
    removed = []
    c.move_up_requested.connect(lambda card: moved_up.append(card))
    c.move_down_requested.connect(lambda card: moved_down.append(card))
    c.remove_requested.connect(lambda card: removed.append(card))
    c._up_btn.click()
    c._down_btn.click()
    c._remove_btn.click()
    assert moved_up == [c]
    assert moved_down == [c]
    assert removed == [c]


def test_set_move_enabled_disables_boundary_buttons(qtbot):
    c = _card(qtbot)
    c.set_move_enabled(up=False, down=True)
    assert not c._up_btn.isEnabled()
    assert c._down_btn.isEnabled()
