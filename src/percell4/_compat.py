"""NumPy 2.0 compatibility shims for third-party packages.

Import this module before any code that uses dtcwt (or other packages
that rely on removed NumPy functions). The two entry points (app.py
and cli/run_pipeline.py) import this at startup.

Remove this file when dtcwt releases a NumPy 2.0-compatible version.
"""

import numpy as np

if not hasattr(np, "asfarray"):
    np.asfarray = lambda a, dtype=np.float64: np.asarray(a, dtype=dtype)  # type: ignore[attr-defined]

if not hasattr(np, "issubsctype"):
    np.issubsctype = lambda arg1, arg2: np.issubdtype(np.result_type(arg1), arg2)  # type: ignore[attr-defined]
