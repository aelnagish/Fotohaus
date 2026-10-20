"""Image canvas used by the editor: fit/zoom/pan, crop box, point/rect picking, face overlay."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

ASPECTS = {"Free": None, "Original": "orig", "Square": 1.0, "4 × 6": 6 / 4, "5 × 7": 7 / 5, "8 × 10": 10 / 8,
           "16 × 9": 16 / 9, "3 × 2": 3 / 2, "4 × 3": 4 / 3}


class ImageCanvas(QWidget):
    clicked_rel = Signal(float, float)          # point in image coordinates (0..1)
    rect_rel = Signal(float, float, float, float)
    face_clicked = Signal(int)
    crop_changed = Signal()
    brushed = Signal(float, float, float)       # x, y, radius (relative to image width)
    brush_done = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.img: QImage | None = None
        self.mode = "view"          # view | crop | point | rect | pick
        self.grid = False
        self.zoom_fit = True
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self.crop: QRectF | None = None   # in relative image coords
        self.aspect = None
        self.faces: list[tuple[int, QRectF, str]] = []
        self.show_faces = False
        self.busy = False
        self.brush_px = 24
        self._drag = None
        self._mouse = None
        self.setMouseTracking(True)
        self.setMinimumSize(300, 200)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- data
    def set_image(self, img: QImage | None, keep_view=True):
        self.img = img
        if not keep_view:
            self.zoom_fit = True
            self.offset = QPointF(0, 0)
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode
        if mode == "crop" and self.img is not None and self.crop is None:
            self.crop = QRectF(0.1, 0.1, 0.8, 0.8)
            self._apply_aspect()
        if mode != "crop":
            self.crop = None
        self.setCursor(Qt.CrossCursor if mode in ("point", "rect", "pick", "crop") else
                       Qt.BlankCursor if mode == "brush" else Qt.ArrowCursor)
        self.update()

    def set_aspect(self, key: str):
        v = ASPECTS.get(key)
        if v == "orig" and self.img is not None:
            v = self.img.width() / max(1, self.img.height())
        self.aspect = v
        self._apply_aspect()
        self.update()

    def _apply_aspect(self):
        if self.crop is None or self.aspect is None or self.img is None:
            return
        iw, ih = self.img.width(), self.img.height()
        c = self.crop
        w_px, h_px = c.width() * iw, c.height() * ih
        a = self.aspect
        # match orientation of the current box
        if (w_px < h_px) != (a < 1):
            a = 1 / a
        if w_px / max(1, h_px) > a:
            w_px = h_px * a
        else:
            h_px = w_px / a
        center = c.center()
        nc = QRectF(0, 0, w_px / iw, h_px / ih)
        nc.moveCenter(center)
        self.crop = self._clamp(nc)
        self.crop_changed.emit()

    def _clamp(self, r: QRectF) -> QRectF:
        r = QRectF(r)
        if r.width() > 1:
            r.setWidth(1)
        if r.height() > 1:
            r.setHeight(1)
        if r.left() < 0:
            r.moveLeft(0)
        if r.top() < 0:
            r.moveTop(0)
        if r.right() > 1:
            r.moveRight(1)
        if r.bottom() > 1:
            r.moveBottom(1)
        return r

    # -- geometry
    def image_rect(self) -> QRectF:
        if self.img is None:
            return QRectF()
        iw, ih = self.img.width(), self.img.height()
        W, H = self.width() - 24, self.height() - 24
        if self.zoom_fit:
            s = min(W / iw, H / ih, 4.0)
        else:
            s = self.zoom
        w, h = iw * s, ih * s
        r = QRectF(0, 0, w, h)
        r.moveCenter(QPointF(self.width() / 2, self.height() / 2) + self.offset)
        return r

    def to_rel(self, pt) -> QPointF:
        r = self.image_rect()
        return QPointF((pt.x() - r.left()) / max(1, r.width()), (pt.y() - r.top()) / max(1, r.height()))

    def from_rel(self, rr: QRectF) -> QRectF:
        r = self.image_rect()
        return QRectF(r.left() + rr.left() * r.width(), r.top() + rr.top() * r.height(),
                      rr.width() * r.width(), rr.height() * r.height())

    # -- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self.palette().window().color().darker(108))
        if self.img is None:
            p.setPen(self.palette().text().color())
            p.drawText(self.rect(), Qt.AlignCenter, "Loading…")
            return
        r = self.image_rect()
        p.drawImage(r, self.img)
        if self.grid:
            p.setPen(QPen(QColor(255, 255, 255, 110), 1))
            n = 8
            for i in range(1, n):
                x = r.left() + r.width() * i / n
                y = r.top() + r.height() * i / n
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        if self.mode == "crop" and self.crop is not None:
            cr = self.from_rel(self.crop)
            path = QPainterPath()
            path.addRect(r)
            hole = QPainterPath()
            hole.addRect(cr)
            p.fillPath(path.subtracted(hole), QColor(0, 0, 0, 140))
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRect(cr)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for i in (1, 2):
                x = cr.left() + cr.width() * i / 3
                y = cr.top() + cr.height() * i / 3
                p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
                p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
            p.setBrush(QColor(255, 255, 255))
            p.setPen(Qt.NoPen)
            for c in (cr.topLeft(), cr.topRight(), cr.bottomLeft(), cr.bottomRight()):
                p.drawEllipse(c, 5, 5)
        if self._drag and self._drag[0] == "rect":
            p.setPen(QPen(QColor(255, 80, 80), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self._drag[1], self._drag[2]).normalized())
        if self.show_faces:
            f = p.font()
            for fid, rr, name in self.faces:
                fr = self.from_rel(rr)
                p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(fr, 4, 4)
                label = name or "Add name"
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(label) + 10
                lr = QRectF(fr.center().x() - tw / 2, fr.bottom() + 4, tw, fm.height() + 4)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 160))
                p.drawRoundedRect(lr, 4, 4)
                p.setPen(Qt.white)
                p.drawText(lr, Qt.AlignCenter, label)
            p.setFont(f)
        if self.mode == "brush" and self._mouse is not None:
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawEllipse(self._mouse, self.brush_px, self.brush_px)
        if self.busy:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 130))
            br = QRectF(self.width() - 110, 12, 96, 24)
            p.drawRoundedRect(br, 12, 12)
            p.setPen(Qt.white)
            p.drawText(br, Qt.AlignCenter, "Rendering…")

    # -- interaction
    def _hit_crop(self, pos):
        if self.crop is None:
            return None
        cr = self.from_rel(self.crop)
        for name, c in (("tl", cr.topLeft()), ("tr", cr.topRight()), ("bl", cr.bottomLeft()), ("br", cr.bottomRight())):
            if (QPointF(pos) - c).manhattanLength() < 14:
                return name
        if cr.contains(QPointF(pos)):
            return "move"
        return None

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() != Qt.LeftButton or self.img is None:
            return
        if self.mode == "crop":
            hit = self._hit_crop(pos)
            if hit:
                self._drag = ("crop", hit, self.to_rel(pos), QRectF(self.crop))
            else:
                rp = self.to_rel(pos)
                self.crop = QRectF(rp, rp)
                self._drag = ("crop", "br", rp, QRectF(self.crop))
            return
        if self.mode in ("point", "pick"):
            rp = self.to_rel(pos)
            if 0 <= rp.x() <= 1 and 0 <= rp.y() <= 1:
                self.clicked_rel.emit(rp.x(), rp.y())
            return
        if self.mode == "rect":
            self._drag = ("rect", pos, pos)
            return
        if self.mode == "brush":
            self._drag = ("brush",)
            self._emit_brush(pos)
            return
        if self.show_faces:
            rp = self.to_rel(pos)
            for fid, rr, name in self.faces:
                if rr.contains(rp):
                    self.face_clicked.emit(fid)
                    return
        if not self.zoom_fit:
            self._drag = ("pan", pos, QPointF(self.offset))

    def _emit_brush(self, pos):
        rp = self.to_rel(pos)
        r = self.image_rect()
        if 0 <= rp.x() <= 1 and 0 <= rp.y() <= 1 and r.width() > 0:
            self.brushed.emit(rp.x(), rp.y(), self.brush_px / r.width())

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.mode == "brush":
            self._mouse = pos
            if self._drag:
                self._emit_brush(pos)
            self.update()
            return
        if not self._drag:
            if self.mode == "crop":
                hit = self._hit_crop(pos)
                self.setCursor({"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                                "bl": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}.get(hit, Qt.CrossCursor))
            return
        kind = self._drag[0]
        if kind == "pan":
            self.offset = self._drag[2] + (pos - self._drag[1])
        elif kind == "rect":
            self._drag = ("rect", self._drag[1], pos)
        elif kind == "crop":
            _, handle, start, orig = self._drag
            rp = self.to_rel(pos)
            rp = QPointF(min(1, max(0, rp.x())), min(1, max(0, rp.y())))
            r = QRectF(orig)
            if handle == "move":
                d = rp - start
                r.translate(d)
                self.crop = self._clamp(r)
            else:
                if "l" in handle:
                    r.setLeft(rp.x())
                if "r" in handle:
                    r.setRight(rp.x())
                if "t" in handle:
                    r.setTop(rp.y())
                if "b" in handle:
                    r.setBottom(rp.y())
                r = r.normalized()
                if self.aspect and self.img is not None:
                    iw, ih = self.img.width(), self.img.height()
                    a = self.aspect
                    if (r.width() * iw < r.height() * ih) != (a < 1):
                        a = 1 / a
                    h = r.width() * iw / a / ih
                    if "t" in handle:
                        r.setTop(r.bottom() - h)
                    else:
                        r.setHeight(h)
                self.crop = r
            self.crop_changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] == "brush":
            self._drag = None
            self.brush_done.emit()
            return
        if self._drag and self._drag[0] == "rect":
            a, b = self.to_rel(self._drag[1]), self.to_rel(e.position())
            rr = QRectF(a, b).normalized()
            if rr.width() > 0.005 and rr.height() > 0.005:
                self.rect_rel.emit(max(0, rr.left()), max(0, rr.top()), min(1, rr.right()), min(1, rr.bottom()))
        if self._drag and self._drag[0] == "crop" and self.crop is not None:
            self.crop = self._clamp(self.crop.normalized())
        self._drag = None
        self.update()

    def _zoom_by(self, factor: float):
        r = self.image_rect()
        cur = r.width() / max(1, self.img.width())
        self.zoom = max(0.05, min(8.0, cur * factor))
        self.zoom_fit = False
        self.update()

    def wheelEvent(self, e):
        if self.img is None:
            return
        zoom_key = bool(e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
        if not zoom_key and not self.zoom_fit and not e.pixelDelta().isNull():
            # two-finger scroll on a trackpad pans a zoomed photo (a mouse wheel still zooms)
            self.offset += QPointF(e.pixelDelta())
            self.update()
        elif zoom_key or not self.zoom_fit:
            self._zoom_by(1.0015 ** e.angleDelta().y())
        else:
            super().wheelEvent(e)

    def event(self, e):
        # pinch to zoom on a trackpad
        if e.type() == QEvent.NativeGesture and self.img is not None \
                and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self._zoom_by(1.0 + e.value())
            return True
        return super().event(e)

    def mouseDoubleClickEvent(self, e):
        if self.mode != "view":
            return
        self.toggle_zoom()

    def toggle_zoom(self):
        if self.zoom_fit:
            self.zoom_fit = False
            self.zoom = 1.0
        else:
            self.zoom_fit = True
            self.offset = QPointF(0, 0)
        self.update()
