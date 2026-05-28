# Analysis Integration Plan

Integrating external Python analysis scripts into percell4 as first-class, reusable analysis modules that consume `.h5` layers instead of `.tif` files.

> **Audience:** an AI coding agent (or developer) implementing this plan in the [percell4](https://github.com/marcusjoshm/percell4) repo. Read top to bottom — sections build on each other. Open questions for the human are marked **[DECIDE]**.

---

## 1. Problem and goals

Today, percell4 generates image data (binary masks, single-cell labels) and stores it in `.h5` datasets. Standalone Python scripts perform downstream measurement and reporting, but they:

- Take `.tif` files as input, requiring manual export from percell4
- Identify input channels by filename keywords (e.g. `"P-body_mask"`, `"Cap"`), forcing users to rename files
- Iterate over directories of grouped tif sets, duplicating logic percell4 already has (dataset/image-set management)

**Goal:** integrate these scripts into percell4 with the same core analysis logic, but:

- Input is a user-specified mapping from analysis *roles* (e.g. `"Cap"`, `"P-body_mask"`) to layer paths in the `.h5` dataset
- Iteration over image sets is handled by percell4, not the script
- Outputs (tables and optional derived images) are written back to percell4-managed locations
- The CLI tif-based workflow continues to work unchanged for legacy use and as a regression test

**Non-goals:**

- Coupling analyses to napari at the logic layer (napari can be one of several callers, but analyses must run headless)
- A full plugin marketplace / hot-loading at runtime — discovery via package entry points or in-repo registration is enough for now
- Reimplementing the analyses themselves; we are refactoring I/O boundaries and adding a registry, not changing what they compute

---

## 2. Design overview

### 2.1 Three layers

```
┌──────────────────────────────────────────────────────────┐
│  Callers                                                  │
│  ──────                                                   │
│  • Interactive launcher (dialog or napari widget)         │
│  • Workflow runner (new "analysis" step type)             │
│  • CLI tif wrappers (existing scripts, kept for legacy)   │
│  • Python API (notebooks, REPL)                           │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│  Analysis registry                                        │
│  ─────────────────                                        │
│  • Base class + decorator for registration                │
│  • Declarative inputs/outputs/parameters/presets          │
│  • Validates layer_map against role declarations          │
│  • Loads h5 layers into arrays, dispatches to run()       │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│  Pure analysis functions                                  │
│  ────────────────────────                                 │
│  • Take ndarray inputs + params, return dict of outputs   │
│  • No file I/O, no directory walks, no globbing           │
│  • Operate on ONE image set at a time                     │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Key concepts

**Role** — a named input slot an analysis declares. Has a human description, expected dtype/shape, and optional constraints. The user provides a `layer_map: dict[role_name, h5_layer_path]` that fills these slots.

**Input group** — a set of roles that are all-or-nothing. E.g. `(P-body_mask, pnorm)` form a "pbody" group: either both are supplied (run the P-body branch) or neither is. Analyses can declare multiple optional groups and a satisfaction policy (`at_least_one`, `exactly_one`, etc).

**Output** — declared just like inputs: name, type (table or image), dtype/shape, and a `produced_when` predicate that references group satisfaction and parameters. The framework uses these declarations to allocate destinations and validate workflows.

**Parameter** — typed scalar input to the analysis. Supports defaults, choices, range constraints, and `requires=[role]` / `requires=[group]` gating (e.g. `single_cell` requires `cp_mask`).

**Preset** — a named, immutable bundle of parameter values for reproducibility. New parameter sets get new keys; existing keys are never edited. Enforced by the registry.

**One image set per `run()` call.** Iteration over image sets is the *workflow's* job, not the analysis's. This is the single most important architectural decision in this plan — see §6.

---

## 3. Proposed module layout

New module: `percell4/analysis/` (adjust to match existing conventions).

```
percell4/analysis/
├── __init__.py
├── types.py          # ImageRole, IntParam, ChoiceParam, TableOutput, ImageOutput, ...
├── base.py           # Analysis base class, register_analysis decorator
├── registry.py       # global registry, lookup, validation, preset enforcement
├── loader.py         # h5 layer -> ndarray helpers
├── runner.py         # invoke a registered analysis given a layer_map + params
├── modules/
│   ├── __init__.py
│   └── per_particle_donut.py   # first migration target
└── tests/
    └── test_per_particle_donut.py
```

**[DECIDE]** Whether `percell4/analysis/` is a new top-level package or a subpackage of an existing one. Match the project's current conventions.

---

## 4. Framework: types and base class

### 4.1 Declarative input/output/parameter types

In `percell4/analysis/types.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

Dtype = Literal["float", "int", "binary", "labels", "any"]

@dataclass(frozen=True)
class ImageRole:
    desc: str
    dtype: Dtype = "any"
    ndim: Optional[tuple[int, ...]] = None  # e.g. (2, 3) means 2D or 3D allowed

@dataclass(frozen=True)
class IntParam:
    default: Optional[int] = None
    min: Optional[int] = None
    max: Optional[int] = None
    help: str = ""

@dataclass(frozen=True)
class FloatParam:
    default: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""

@dataclass(frozen=True)
class BoolParam:
    default: Optional[bool] = None
    help: str = ""
    requires: tuple[str, ...] = ()   # role or group names that must be satisfied

@dataclass(frozen=True)
class ChoiceParam:
    choices: tuple[str, ...]
    default: Optional[str] = None
    help: str = ""

Param = IntParam | FloatParam | BoolParam | ChoiceParam

@dataclass(frozen=True)
class TableOutput:
    desc: str = ""
    produced_when: Optional[str] = None   # see §4.3 for predicate syntax

@dataclass(frozen=True)
class ImageOutput:
    desc: str = ""
    dtype: Dtype = "any"
    produced_when: Optional[str] = None

Output = TableOutput | ImageOutput
```

### 4.2 Base class and decorator

In `percell4/analysis/base.py`:

```python
from __future__ import annotations
from typing import Any, ClassVar
import numpy as np
from .types import ImageRole, Param, Output

class Analysis:
    """Base class for all registered analyses.

    Subclasses declare their schema as class attributes and implement `run`.
    The framework loads h5 layers into ndarrays, validates the layer_map and
    parameters, applies presets, and calls `run(inputs, params)`.
    """
    name: ClassVar[str]                    # registry key, e.g. "per_particle_donut"
    display_name: ClassVar[str] = ""
    version: ClassVar[str] = "1.0.0"

    required_inputs: ClassVar[dict[str, ImageRole]] = {}
    input_groups: ClassVar[dict[str, dict[str, ImageRole]]] = {}
    group_requirement: ClassVar[str] = "at_least_one"   # or "exactly_one", "all"
    optional_inputs: ClassVar[dict[str, ImageRole]] = {}

    parameters: ClassVar[dict[str, Param]] = {}
    presets: ClassVar[dict[str, dict[str, Any]]] = {}
    outputs: ClassVar[dict[str, Output]] = {}

    def run(
        self,
        inputs: dict[str, np.ndarray],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Pure function: arrays + params in, named outputs out.

        `inputs` contains exactly the roles satisfied by the caller's
        layer_map (required + any group whose roles were all supplied +
        any optional roles that were supplied).

        Returns a dict keyed by output name; values are either
        pandas.DataFrame (for TableOutput) or np.ndarray (for ImageOutput).
        Only outputs whose `produced_when` predicate evaluates True should
        appear in the dict.
        """
        raise NotImplementedError
```

The decorator just registers the class:

```python
def register_analysis(name: str):
    def decorator(cls: type[Analysis]) -> type[Analysis]:
        cls.name = name
        from .registry import _REGISTRY
        if name in _REGISTRY:
            raise ValueError(f"Analysis {name!r} already registered")
        _REGISTRY[name] = cls
        return cls
    return decorator
```

### 4.3 The `produced_when` predicate

A small string DSL is enough; full Python `eval` is overkill and unsafe. Supported tokens:

- `<group_name>_group_satisfied` — true if every role in that group was supplied
- `<role_name>_supplied` — true if that role was supplied
- `<param_name>` — true if the parameter is truthy
- Boolean operators: `and`, `or`, `not`, parentheses

Examples used by the first migration:

```python
produced_when = "pbody_group_satisfied"
produced_when = "export_donuts and sg_group_satisfied"
```

Implement as a tiny recursive-descent parser or, simpler, build a `dict` namespace of booleans and use `eval(expr, {"__builtins__": {}}, namespace)`. The namespace is fully controlled, so `eval` is acceptable here — but document this carefully.

### 4.4 Registry responsibilities

In `percell4/analysis/registry.py`, the registry must:

1. **Validate schemas at registration time.** Every preset key references a real parameter; every `produced_when` predicate uses known group/role/param names; every `requires` on a `BoolParam` references a real role or group; no required input also appears in a group; no role name collides across required/groups/optional.
2. **Enforce preset immutability.** Persist a content hash of each preset (e.g. `<repo>/percell4/analysis/preset_hashes.json`, committed) and refuse registration if an existing preset key's content has changed. New parameter values → new key. Adding new preset keys is fine.
3. **Provide lookup.** `get(name) -> type[Analysis]`, `list_analyses() -> list[AnalysisInfo]` (name, display name, summary of inputs/params, for UIs).

### 4.5 Loader and runner

`loader.py` reads layers from h5 to ndarray, applies dtype coercion based on the role's declared `dtype` (binary → bool, labels → int, float → float64), and validates `ndim`.

`runner.py` is the single entry point all callers use:

```python
def run_analysis(
    analysis_name: str,
    h5_path: str,
    layer_map: dict[str, str],   # role -> h5 layer path
    params: dict[str, Any] | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    """Validate, load layers, apply preset, invoke run(), return outputs.

    Does NOT write outputs anywhere — the caller decides where they go.
    """
```

Validation order: resolve preset (error if both `preset` and overlapping `params`), determine which groups are satisfied, check group_requirement, check every `BoolParam`'s `requires`, load arrays, call `run()`, validate returned dict against declared outputs.

---

## 5. First migration target: per-particle donut analysis

The script `per_particle_analysis.py` becomes `percell4/analysis/modules/per_particle_donut.py`.

### 5.1 Refactor the existing script first

Before writing the registered analysis, fix two I/O leaks in the original script. These changes preserve CLI behavior — the CLI wrapper reads tifs and passes arrays.

**Change 1:** `analyze_regions(mask_path, cap_img, norm_img, ...)` → `analyze_regions(mask_img, cap_img, norm_img, ...)`. Move `mask_img = tifffile.imread(mask_path)` out of the function and into `main()`.

**Change 2:** `assign_particles_to_cells(mask_path, cp_mask_img, min_size)` → `assign_particles_to_cells(mask_img, cp_mask_img, min_size)`. Same treatment.

**Change 3:** Remove the donut TIFF write from inside `analyze_regions`. Return the donut mask as part of the result instead:

```python
return {
    "rows": results,                  # list[dict] of per-particle measurements
    "donut_mask": donut_export,       # ndarray or None
}
```

The CLI wrapper writes the tif if `--export-donuts` is set; the registered analysis returns it as a layer output.

**Change 4:** Extract a function `run_one_image_set(arrays, params) -> dict` containing everything currently inside the `for group_key, channels in sorted(groups.items()):` loop. Signature:

```python
def run_one_image_set(
    *,
    cap: np.ndarray,
    pbody_mask: np.ndarray | None = None,
    pnorm: np.ndarray | None = None,
    sg_mask: np.ndarray | None = None,
    sgnorm: np.ndarray | None = None,
    cp_mask: np.ndarray | None = None,
    # params
    buffer: int, donut: int, bg_mode: str, bg_value: int,
    exclude_cap_zero: bool, min_size: int, bgsub_k: float,
    no_bgsub: bool, single_cell: bool, export_donuts: bool,
) -> dict[str, Any]:
    """Returns dict with keys (only those produced):
       pbody_rows, sg_rows, pbody_donut_mask, sg_donut_mask
    """
```

This function holds the mode-detection logic (has_pbody / has_sg), the global background subtraction (`estimate_bg_threshold`), the SG-before-P-body exclusion, and the optional single-cell aggregation. **It does not loop over image sets and does not write CSVs.**

The original `main()` becomes a thin wrapper: parse args, walk the directory with `group_image_sets`, for each group call `run_one_image_set`, attach `group_key` to rows, concatenate, write CSVs. This is the regression test.

### 5.2 The registered analysis

```python
# percell4/analysis/modules/per_particle_donut.py
import numpy as np
import pandas as pd
from ..base import Analysis, register_analysis
from ..types import (
    ImageRole, IntParam, FloatParam, BoolParam, ChoiceParam,
    TableOutput, ImageOutput,
)
# Import the pure logic from the refactored script:
from percell4.analysis._impl.per_particle_donut import run_one_image_set

@register_analysis("per_particle_donut")
class PerParticleDonut(Analysis):
    display_name = "Per-particle donut background subtraction"
    version = "1.0.0"

    required_inputs = {
        "Cap": ImageRole("Cap intensity channel", dtype="float"),
    }
    input_groups = {
        "pbody": {
            "P-body_mask": ImageRole("P-body binary mask", dtype="binary"),
            "pnorm":       ImageRole("P-body normalization channel", dtype="float"),
        },
        "sg": {
            "SG_mask": ImageRole("Stress granule binary mask", dtype="binary"),
            "sgnorm":  ImageRole("SG normalization channel", dtype="float"),
        },
    }
    group_requirement = "at_least_one"
    optional_inputs = {
        "cp_mask": ImageRole("Cellpose cell labels", dtype="labels"),
    }
    parameters = {
        "buffer":           IntParam(default=4, min=0),
        "donut":            IntParam(default=5, min=1),
        "bg_mode":          ChoiceParam(("donut", "donut-mean", "flat"), default="donut"),
        "bg_value":         IntParam(default=1, min=0),
        "exclude_cap_zero": BoolParam(default=True),
        "min_size":         IntParam(default=10, min=0),
        "bgsub_k":          FloatParam(default=2.5, min=0.0),
        "no_bgsub":         BoolParam(default=False),
        "single_cell":      BoolParam(default=False, requires=("cp_mask",)),
        "export_donuts":    BoolParam(default=False),
    }
    presets = {
        # Never modify. New parameter values = new key (e.g. m7g-cap-v2).
        "m7g-cap-v1": {
            "buffer": 5, "donut": 5, "bg_mode": "donut-mean",
            "min_size": 4, "bgsub_k": 2.5, "no_bgsub": False,
            "bg_value": 1, "exclude_cap_zero": True, "export_donuts": False,
        },
    }
    outputs = {
        "pbody_table":      TableOutput("P-body per-particle (or per-cell) results",
                                        produced_when="pbody_group_satisfied"),
        "sg_table":         TableOutput("SG per-particle (or per-cell) results",
                                        produced_when="sg_group_satisfied"),
        "pbody_donut_mask": ImageOutput("P-body donut mask", dtype="binary",
                                        produced_when="export_donuts and pbody_group_satisfied"),
        "sg_donut_mask":    ImageOutput("SG donut mask", dtype="binary",
                                        produced_when="export_donuts and sg_group_satisfied"),
    }

    def run(self, inputs, params):
        result = run_one_image_set(
            cap=inputs["Cap"],
            pbody_mask=inputs.get("P-body_mask"),
            pnorm=inputs.get("pnorm"),
            sg_mask=inputs.get("SG_mask"),
            sgnorm=inputs.get("sgnorm"),
            cp_mask=inputs.get("cp_mask"),
            **params,
        )
        # Pack into output names declared above. Convert row lists to DataFrames.
        out: dict = {}
        if result.get("pbody_rows") is not None:
            out["pbody_table"] = pd.DataFrame(result["pbody_rows"])
        if result.get("sg_rows") is not None:
            out["sg_table"] = pd.DataFrame(result["sg_rows"])
        if result.get("pbody_donut_mask") is not None:
            out["pbody_donut_mask"] = result["pbody_donut_mask"]
        if result.get("sg_donut_mask") is not None:
            out["sg_donut_mask"] = result["sg_donut_mask"]
        return out
```

### 5.3 What gets deleted (or moved)

- `parse_filename`, `group_image_sets`: only used by the CLI wrapper. Stay in the CLI module.
- `save_results`, `save_single_cell_results`: same — CLI-only. Multi-image-set CSV aggregation moves to the workflow runner (see §6).
- The `group` column on result rows: not attached inside `run_one_image_set` anymore. Attached by the caller (CLI wrapper, or workflow runner) using its own knowledge of image-set identity.

---

## 6. Workflow integration

Iteration over image sets is the workflow's job. Add an `analysis` step type to percell4 workflows.

### 6.1 Schema

```yaml
- type: analysis
  module: per_particle_donut
  preset: m7g-cap-v1          # optional, mutually exclusive with `params`
  params:                      # optional, used when preset is absent or for overrides
    single_cell: true
  inputs:                      # role -> h5 layer path
    Cap:           channel_0/raw
    P-body_mask:   segmentation/pbody_mask
    pnorm:         channel_1/raw
    SG_mask:       segmentation/sg_mask
    sgnorm:        channel_2/raw
    cp_mask:       segmentation/cell_labels
  outputs:                     # output name -> destination
    pbody_table:      reports/pbody.csv      # csv path, relative to project root
    sg_table:         reports/sg.csv
    pbody_donut_mask: layers/pbody_donut     # h5 layer group/name
    sg_donut_mask:    layers/sg_donut
  iterate_over: image_sets     # default; runs analysis per image set in the project
  concatenate_tables: true     # tables get a `group` (image_set_id) column and are
                               # concatenated across image sets into one CSV
```

**[DECIDE]** Whether output paths are project-relative, dataset-relative, or both supported. Match existing workflow conventions in percell4.

**[DECIDE]** Whether to allow `params` overrides when `preset` is set, or to keep the original strict behavior (preset locks all params). I'd suggest *strict by default* — matches the script's current rule and protects reproducibility — with an explicit `preset_overrides:` key if escape hatch is needed.

### 6.2 Runner behavior

For each image set in the project:

1. Resolve `inputs` (the layer paths may use templates referencing image_set name).
2. Call `run_analysis(module, h5_path, layer_map, params, preset)`.
3. For each returned `TableOutput`, prepend a `group` (or `image_set_id`) column and append to an accumulator keyed by output destination.
4. For each returned `ImageOutput`, write to the destination h5 layer (per image set).
5. After all image sets, write each accumulated CSV.

### 6.3 Interactive launcher

A dialog (or napari widget) lists registered analyses from `list_analyses()`. On selection, it presents:

- One layer dropdown per required role
- One layer dropdown per optional role (with "not provided" option)
- One layer-pair per input group (grouped visually, with a "skip this group" option that hides both)
- Parameter controls inferred from the parameter types; preset dropdown above them that disables manual controls when active
- An output destination panel (with sensible defaults derived from analysis name + image set name)

Disabled controls when their `requires` aren't met (e.g. `single_cell` greyed out until `cp_mask` is assigned).

---

## 7. Implementation plan (ordered tasks)

Each task lists files touched and acceptance criteria. Tasks within a phase can be parallelized; phases are sequential.

### Phase 1 — Framework scaffolding

**Task 1.1: Create `percell4/analysis/` package with `types.py`.**
Acceptance: types import cleanly; dataclasses are frozen; unit test instantiates one of each.

**Task 1.2: Implement `base.py` with `Analysis` and `register_analysis`.**
Acceptance: decorating a stub subclass populates `_REGISTRY[name]`; duplicate names raise.

**Task 1.3: Implement `registry.py` with schema validation.**
Acceptance: registering an analysis whose preset references an unknown parameter raises with a clear message. Registering one whose `produced_when` references an unknown group raises. Tests cover both.

**Task 1.4: Implement preset hash enforcement.**
Acceptance: changing a preset's contents (without renaming the key) raises at registration; adding a new preset key does not; the hash file is human-readable JSON committed to the repo.

**Task 1.5: Implement the `produced_when` predicate evaluator.**
Acceptance: tests cover `and`/`or`/`not`/parens; unknown tokens raise at registration; `eval` uses an empty `__builtins__` namespace.

**Task 1.6: Implement `loader.py`.**
Acceptance: given an h5 path and a `dict[role, layer_path]` plus role declarations, returns `dict[role, ndarray]` with correct dtype coercion; missing layers raise; dtype mismatches raise.

**Task 1.7: Implement `runner.run_analysis`.**
Acceptance: end-to-end test using a stub analysis confirms group satisfaction logic, parameter merging with presets, dtype coercion, and that returned outputs match declared outputs.

### Phase 2 — Refactor `per_particle_analysis.py`

**Task 2.1:** Apply Changes 1–3 from §5.1 (remove I/O from `analyze_regions` and `assign_particles_to_cells`; return donut mask instead of writing it). CLI behavior unchanged.
Acceptance: existing CLI produces byte-identical CSV output on a test fixture before and after the refactor. *(Build the fixture as part of this task — a small directory of tifs with known expected CSV.)*

**Task 2.2:** Extract `run_one_image_set` per §5.1 Change 4. Move pure logic into `percell4/analysis/_impl/per_particle_donut.py`.
Acceptance: CLI still produces byte-identical output; `run_one_image_set` is unit-tested directly with synthetic arrays for each of the three modes (P-body only, SG only, both) and with/without `single_cell`.

### Phase 3 — Wire up the registered analysis

**Task 3.1:** Implement `percell4/analysis/modules/per_particle_donut.py` per §5.2.
Acceptance: `run_analysis("per_particle_donut", ...)` on a test h5 produces results numerically identical to the CLI on the equivalent tif inputs.

**Task 3.2:** Add the `m7g-cap-v1` preset hash to the committed hash file.
Acceptance: changing any preset value in code raises at import; reverting passes.

### Phase 4 — Workflow integration

**Task 4.1:** Add the `analysis` step type to the workflow runner per §6.
Acceptance: a workflow yaml that runs `per_particle_donut` across two image sets produces a concatenated CSV with `group` column and (if `export_donuts: true`) layers written to both image sets' h5.

**Task 4.2:** Wire optional ImageOutput writes to h5 layers per the workflow's `outputs:` mapping.
Acceptance: round-trip — read written donut layer back, compare to expected mask.

### Phase 5 — Interactive launcher

**Task 5.1:** Build the dialog/widget per §6.3. Start with a plain Qt or magicgui dialog; napari integration can be a thin wrapper later.
Acceptance: manual smoke test on one h5 dataset; user can run `per_particle_donut` end-to-end without editing yaml.

### Phase 6 — Documentation and second analysis

**Task 6.1:** Add `docs/writing_an_analysis.md` walking through the per-particle module as a worked example.

**Task 6.2:** Migrate a second script to validate the framework's generality. The first migration is allowed to bend the framework; the second migration is where the framework's design gets stress-tested. **[DECIDE]** which script is second.

---

## 8. Testing strategy

- **Regression fixture for the original CLI.** A small `tests/fixtures/per_particle/` directory with input tifs and expected CSVs. Re-run before and after every Phase 2 task; outputs must be byte-identical.
- **Synthetic-array unit tests for `run_one_image_set`.** Hand-constructed small images with known particle counts, areas, and intensities; assert exact expected metrics. Cover all three modes and `single_cell`.
- **Framework tests for the registry.** Bad schemas (unknown preset params, undefined groups in predicates, name collisions) must raise with messages naming the offending field.
- **End-to-end h5 test.** A small synthetic h5 with known layers; `run_analysis` produces the same numbers as the CLI on equivalent tifs.
- **Preset immutability test.** A test that tries to change a known preset's value and asserts registration fails.

---

## 9. Backwards compatibility

- Existing `per_particle_analysis.py` CLI keeps working. After Phase 2 it imports `run_one_image_set` from the new module path but exposes the same flags, defaults, and output format.
- Other unmigrated scripts continue to work standalone. Migration is per-script, not all-or-nothing.
- The percell4 dataset format itself is not changed by this plan.

---

## 10. Open questions

1. **[DECIDE]** Top-level package location for `analysis/` (see §3).
2. **[DECIDE]** Workflow output destination conventions — project-relative vs dataset-relative (§6.1).
3. **[DECIDE]** Whether preset+params overrides are allowed (§6.1); recommendation is strict-by-default.
4. **[DECIDE]** What `image_set_id` actually looks like in the existing percell4 dataset model. The workflow runner needs a stable identifier to populate the `group` column on concatenated CSVs.
5. **[DECIDE]** How layer paths inside an h5 are typically structured in percell4 today. The interactive launcher's dropdown population and the workflow yaml's path templating both depend on this.
6. **[DECIDE]** Whether to support 3D inputs from day one (the current script is 2D-only — `mask_img.shape` is unpacked as `h, w`). For the first migration, document 2D-only; for the framework, leave `ndim` in `ImageRole` to allow future 3D analyses.
7. **[DECIDE]** Which script becomes the second migration target (§7, Phase 6).

---

## 11. Summary of design decisions baked into this plan

- Analyses are **declared, not just coded** — schema lives in class attributes so callers (workflow, launcher, API) can introspect.
- Analyses **operate on one image set**; iteration belongs to the workflow runner.
- Inputs declare **roles**, not filenames; users supply a `layer_map` from role to h5 layer path.
- **Input groups** capture "all-or-nothing" optionality (P-body vs SG branches).
- **Outputs are declared** with `produced_when` predicates so callers know what to allocate.
- **Presets are immutable** and hash-enforced at registration.
- **CLI compatibility is preserved** as a regression test, not as a parallel codebase to maintain.
- **No napari coupling at the logic layer** — napari is one possible caller, not a dependency.
