"""Vendored third-party computational cores, pinned for reproducibility.

Subpackages here are copies of external code brought into the domain layer so the
import-linter and `tests/test_qt_free_imports.py` keep `percell4.domain` free of
infrastructure and I/O dependencies. Vendored code is trimmed to its pure-numpy
computational subset only.
"""
