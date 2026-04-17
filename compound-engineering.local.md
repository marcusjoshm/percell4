---
review_agents:
  - kieran-python-reviewer
  - architecture-strategist
  - code-simplicity-reviewer
  - performance-oracle
  - pattern-recognition-specialist
---

Python 3.12 / Qt (qtpy + PyQt5/6) desktop app for single-cell microscopy analysis. Uses napari for image viewing, pyqtgraph for plots, HDF5 for storage. This branch is a hexagonal architecture refactor: domain/, application/, ports/, adapters/, interfaces/. Key concern: is the hex seam clean and the architecture actually simpler than what it replaced?
