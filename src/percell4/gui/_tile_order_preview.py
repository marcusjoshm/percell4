"""A small 3x3 diagram of the tile acquisition order.

Shows, for the currently selected Pattern and Order, the path the acquisition
took across the grid — the same affordance the Fiji Grid/Collection Stitching
plugin provides, and the fastest way to catch a wrong pick before it silently
produces a scrambled mosaic.

The traversal is **not** reimplemented here. It comes from
``domain.io.assembler._tile_positions``, the same function the importer uses to
place real tiles, so the picture cannot disagree with what the software will
actually do. Redrawing the walk by hand in the GUI would be precisely the
duplication this refactor exists to remove.
"""

from __future__ import annotations

import math

from qtpy.QtCore import QPointF, QRectF, QSize, Qt
from qtpy.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from qtpy.QtWidgets import QWidget

from percell4.domain.io.assembler import _tile_positions
from percell4.gui import theme

# A 3x3 preview is enough to disambiguate every Pattern/Order pair: it has a
# middle row and column, so snake reversal and the stepping direction are both
# visible. It is a fixed illustration and does not track Grid size X/Y.
_N = 3

_CELL = 26
_GAP = 4
_MARGIN = 8


class TileOrderPreview(QWidget):
    """Draws the acquisition path for a ``(grid_type, order)`` pair."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        grid_type: str = "row_by_row",
        order: str = "top_left",
    ) -> None:
        super().__init__(parent)
        self._grid_type = grid_type
        self._order = order
        side = _MARGIN * 2 + _N * _CELL + (_N - 1) * _GAP
        self.setFixedSize(side, side)
        self.setToolTip(
            "Acquisition order for the selected Pattern and Order.\n"
            "The dot marks the first tile; the arrow marks the last."
        )

    def set_pattern(self, grid_type: str, order: str) -> None:
        """Update the illustrated walk. Unknown values leave it unchanged."""
        if grid_type == self._grid_type and order == self._order:
            return
        try:
            _tile_positions(_N, _N, grid_type, order)
        except ValueError:
            return
        self._grid_type = grid_type
        self._order = order
        self.update()

    # ── Painting ────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        return self.size()

    def _cell_center(self, row: int, col: int) -> QPointF:
        return QPointF(
            _MARGIN + col * (_CELL + _GAP) + _CELL / 2,
            _MARGIN + row * (_CELL + _GAP) + _CELL / 2,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        try:
            positions = _tile_positions(_N, _N, self._grid_type, self._order)
        except ValueError:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Cells.
        painter.setPen(QPen(QColor(theme.BORDER_INPUT), 1))
        painter.setBrush(QColor(theme.SURFACE))
        for row in range(_N):
            for col in range(_N):
                painter.drawRect(
                    QRectF(
                        _MARGIN + col * (_CELL + _GAP),
                        _MARGIN + row * (_CELL + _GAP),
                        _CELL,
                        _CELL,
                    )
                )

        # The walk, in acquisition order.
        points = [self._cell_center(*positions[i]) for i in range(_N * _N)]
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(
            QPen(QColor(theme.ACCENT), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        )
        painter.drawPath(path)

        # Start marker.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawEllipse(points[0], 3.5, 3.5)

        # Arrowhead on the final leg, so the direction of travel is explicit.
        self._draw_arrow_head(painter, points[-2], points[-1])
        painter.end()

    @staticmethod
    def _draw_arrow_head(painter: QPainter, start: QPointF, end: QPointF) -> None:
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length == 0:
            return
        angle = math.atan2(dy, dx)
        size = 7.0
        spread = math.radians(26)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawPolygon(
            QPolygonF(
                [
                    end,
                    QPointF(
                        end.x() - size * math.cos(angle - spread),
                        end.y() - size * math.sin(angle - spread),
                    ),
                    QPointF(
                        end.x() - size * math.cos(angle + spread),
                        end.y() - size * math.sin(angle + spread),
                    ),
                ]
            )
        )
