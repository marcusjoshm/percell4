# docs/reference/

Current, non-planning reference material — external-format docs (e.g. `.docx`
design guides), third-party protocol notes, and any long-form writeups that
are still accurate but don't belong in `CLAUDE.md` (too detailed) or
`docs/solutions/` (not a solved-problem postmortem).

Files here describe **current state** only. Anything that has been superseded
must move to `docs/archive/` with a `SUPERSEDED` banner.

If a file in here gets out of date, either update it or archive it — do not
let stale references accumulate.

## Contents

- [`lif-xml-header.md`](lif-xml-header.md) — the Leica `.lif` XML header:
  container layout, element tree, where the FLIM phasor calibration lives, and
  an index of every tag. Read this before touching
  `src/percell4/domain/io/lif_*.py`; it documents the two competing calibration
  records and which one is correct.
- [`lif-xml-header.xml`](lif-xml-header.xml) — the full pretty-printed header of
  the reference file, for grep.

