"""Colors, stylesheet and vector icons (drawn in code — no third-party icon assets)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPalette, QGuiApplication

ACCENT = QColor("#2f7de1")
STAR = QColor("#f5b301")


def scaled_font(font, factor: float, bold: bool | None = None):
    from PySide6.QtGui import QFont
    f = QFont(font)
    if f.pointSizeF() > 0:
        f.setPointSizeF(max(6.0, f.pointSizeF() * factor))
    elif f.pixelSize() > 0:
        f.setPixelSize(max(8, int(round(f.pixelSize() * factor))))
    if bold is not None:
        f.setBold(bold)
    return f


def is_dark() -> bool:
    pal = QGuiApplication.palette()
    return pal.color(QPalette.Window).lightness() < 128


def fg() -> QColor:
    return QColor("#e8e8ea") if is_dark() else QColor("#3a3d42")


def stylesheet() -> str:
    dark = is_dark()
    side_bg = "#232427" if dark else "#eef0f3"
    side_fg = "#d9d9dc" if dark else "#2c2f33"
    sec = "#8c8f96" if dark else "#7b7f87"
    grid_bg = "#1b1c1e" if dark else "#ffffff"
    bar_bg = "#26272a" if dark else "#f6f7f9"
    border = "#3a3b3f" if dark else "#d9dce1"
    card_bg = "#2a2b2f" if dark else "#ffffff"
    chip_bg = "#33353a" if dark else "#f1f2f5"
    person_bg = "#1f3550" if dark else "#e8f1fd"
    person_border = "#2f5a8f" if dark else "#bcd5f5"
    star_bg = "#4a3b10" if dark else "#fff6dc"
    star_border = "#8a6a10" if dark else "#f2d27a"
    return f"""
    QTreeWidget#Sidebar {{ background: {side_bg}; color: {side_fg}; border: none; font-size: 13px; }}
    QTreeWidget#Sidebar {{ outline: 0; show-decoration-selected: 0; }}
    QTreeWidget#Sidebar::item {{ padding: 3px 2px; border: none; }}
    QTreeWidget#Sidebar::item:selected, QTreeWidget#Sidebar::item:selected:active,
    QTreeWidget#Sidebar::item:selected:!active {{ background: transparent; color: white; }}
    QTreeWidget#Sidebar::item:hover:!selected {{ background: transparent; }}
    QTreeWidget#Sidebar::branch, QTreeWidget#Sidebar::branch:selected {{ background: transparent; }}
    QListView#Grid {{ background: {grid_bg}; border: none; }}
    QWidget#BottomBar, QWidget#EditorBar {{ background: {bar_bg}; border-top: 1px solid {border}; }}
    QWidget#InfoPanel {{ background: {bar_bg}; border-left: 1px solid {border}; }}
    QLabel#Section {{ color: {sec}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
    QLabel#Title {{ font-size: 18px; font-weight: 600; }}
    QLabel#Muted {{ color: {sec}; }}
    QWidget#RightPanel {{ background: {bar_bg}; border-left: 1px solid {border}; }}
    QFrame#InfoCard {{ background: {card_bg}; border: 1px solid {border}; border-radius: 10px; }}
    QFrame#InfoCard QLabel {{ background: transparent; border: none; }}
    QLabel#InfoTitle {{ font-size: 15px; font-weight: 600; }}
    QLineEdit#InfoField {{ border: 1px solid {border}; border-radius: 6px; padding: 5px 8px;
                           background: {grid_bg}; }}
    QLineEdit#InfoField:focus {{ border: 1px solid {ACCENT.name()}; }}
    QPushButton#StarPill {{ border: 1px solid {border}; border-radius: 13px; padding: 4px 14px;
                            background: {card_bg}; min-height: 18px; }}
    QPushButton#StarPill:checked {{ background: {star_bg}; border-color: {star_border}; }}
    QPushButton#LinkButton {{ border: none; background: transparent; color: {ACCENT.name()}; padding: 2px 0; }}
    QPushButton#LinkButton:hover {{ text-decoration: underline; }}
    QPushButton#Chip, QPushButton#ChipAlbum, QPushButton#ChipPerson {{ border-radius: 10px; padding: 3px 10px;
                            border: 1px solid {border}; background: {chip_bg}; }}
    QPushButton#ChipPerson {{ background: {person_bg}; border-color: {person_border}; }}
    QPushButton#ChipPerson:hover {{ border-color: {ACCENT.name()}; }}
    QLineEdit#Search {{ border-radius: 12px; padding: 4px 10px; min-width: 260px;
                        border: 1px solid {border}; background: {grid_bg}; }}
    QPushButton#Effect {{ text-align: left; padding: 6px 10px; }}
    QToolButton {{ padding: 4px 6px; }}
    QTabWidget#EditTabs::pane {{ border: none; }}
    """


# ------------------------------------------------------------------ icon painter

def _star_path(cx, cy, r_out, r_in, points=5) -> QPainterPath:
    p = QPainterPath()
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + i * math.pi / points
        pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        if i == 0:
            p.moveTo(pt)
        else:
            p.lineTo(pt)
    p.closeSubpath()
    return p


def star_path(rect: QRectF) -> QPainterPath:
    s = min(rect.width(), rect.height())
    return _star_path(rect.center().x(), rect.center().y() + s * 0.04, s * 0.5, s * 0.21)


def _draw(name: str, p: QPainter, s: float, color: QColor) -> None:
    pen = QPen(color, s * 0.075, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = s * 0.16
    r = QRectF(m, m, s - 2 * m, s - 2 * m)
    c = r.center()
    if name == "star":
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawPath(_star_path(c.x(), c.y() + s * 0.03, s * 0.36, s * 0.15))
    elif name == "star-outline":
        p.drawPath(_star_path(c.x(), c.y() + s * 0.03, s * 0.36, s * 0.15))
    elif name in ("rotate-left", "rotate-right"):
        sign = -1 if name == "rotate-left" else 1
        arc = QRectF(c.x() - s * 0.27, c.y() - s * 0.24, s * 0.54, s * 0.54)
        start = 90 if sign < 0 else 90
        span = 270 * (-1 if sign > 0 else 1) * -1
        path = QPainterPath()
        path.arcMoveTo(arc, start)
        path.arcTo(arc, start, span if sign < 0 else -270)
        p.drawPath(path)
        tip = path.pointAtPercent(0.0)
        dx = s * 0.12
        p.drawLine(tip, QPointF(tip.x() + sign * dx, tip.y() - dx * 0.9))
        p.drawLine(tip, QPointF(tip.x() + sign * dx, tip.y() + dx * 0.9))
    elif name == "trash":
        p.drawLine(QPointF(r.left() + s * 0.05, r.top() + s * 0.12), QPointF(r.right() - s * 0.05, r.top() + s * 0.12))
        p.drawLine(QPointF(c.x() - s * 0.08, r.top()), QPointF(c.x() + s * 0.08, r.top()))
        body = QPainterPath()
        body.moveTo(r.left() + s * 0.12, r.top() + s * 0.2)
        body.lineTo(r.left() + s * 0.18, r.bottom())
        body.lineTo(r.right() - s * 0.18, r.bottom())
        body.lineTo(r.right() - s * 0.12, r.top() + s * 0.2)
        p.drawPath(body)
    elif name == "play":
        path = QPainterPath()
        path.moveTo(r.left() + s * 0.14, r.top() + s * 0.04)
        path.lineTo(r.right() - s * 0.02, c.y())
        path.lineTo(r.left() + s * 0.14, r.bottom() - s * 0.04)
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)
    elif name == "pause":
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(r.left() + s * 0.1, r.top(), s * 0.18, r.height()), s * 0.03, s * 0.03)
        p.drawRoundedRect(QRectF(r.right() - s * 0.28, r.top(), s * 0.18, r.height()), s * 0.03, s * 0.03)
    elif name == "close":
        p.drawLine(QPointF(r.left() + s * 0.08, r.top() + s * 0.08), QPointF(r.right() - s * 0.08, r.bottom() - s * 0.08))
        p.drawLine(QPointF(r.right() - s * 0.08, r.top() + s * 0.08), QPointF(r.left() + s * 0.08, r.bottom() - s * 0.08))
    elif name == "zoom-in" or name == "zoom-out":
        p.drawEllipse(QRectF(r.left(), r.top(), r.width() * 0.7, r.height() * 0.7))
        p.drawLine(QPointF(r.left() + r.width() * 0.6, r.top() + r.height() * 0.6), r.bottomRight())
        cc = QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.35)
        p.drawLine(QPointF(cc.x() - s * 0.1, cc.y()), QPointF(cc.x() + s * 0.1, cc.y()))
        if name == "zoom-in":
            p.drawLine(QPointF(cc.x(), cc.y() - s * 0.1), QPointF(cc.x(), cc.y() + s * 0.1))
    elif name == "share":
        p.drawRoundedRect(QRectF(r.left() + s * 0.08, c.y() - s * 0.08, r.width() - s * 0.16, r.height() / 2 + s * 0.08), s * 0.05, s * 0.05)
        p.drawLine(QPointF(c.x(), r.top() - s * 0.02), QPointF(c.x(), c.y() + s * 0.1))
        p.drawLine(QPointF(c.x(), r.top() - s * 0.02), QPointF(c.x() - s * 0.1, r.top() + s * 0.08))
        p.drawLine(QPointF(c.x(), r.top() - s * 0.02), QPointF(c.x() + s * 0.1, r.top() + s * 0.08))
    elif name == "mail":
        p.drawRoundedRect(QRectF(r.left(), r.top() + s * 0.1, r.width(), r.height() - s * 0.2), s * 0.04, s * 0.04)
        p.drawLine(QPointF(r.left(), r.top() + s * 0.12), QPointF(c.x(), c.y() + s * 0.04))
        p.drawLine(QPointF(r.right(), r.top() + s * 0.12), QPointF(c.x(), c.y() + s * 0.04))
    elif name == "print":
        p.drawRect(QRectF(r.left() + s * 0.15, r.top(), r.width() - s * 0.3, s * 0.2))
        p.drawRoundedRect(QRectF(r.left(), r.top() + s * 0.2, r.width(), r.height() * 0.45), s * 0.04, s * 0.04)
        p.drawRect(QRectF(r.left() + s * 0.15, c.y() + s * 0.08, r.width() - s * 0.3, r.bottom() - c.y() - s * 0.08))
    elif name == "movie":
        p.drawRoundedRect(QRectF(r.left(), r.top() + s * 0.08, r.width(), r.height() - s * 0.16), s * 0.04, s * 0.04)
        for i in range(4):
            x = r.left() + s * 0.06 + i * r.width() / 4
            p.drawLine(QPointF(x, r.top() + s * 0.08), QPointF(x + s * 0.08, r.top() + s * 0.2))
        path = QPainterPath()
        path.moveTo(c.x() - s * 0.08, c.y() - s * 0.02)
        path.lineTo(c.x() + s * 0.12, c.y() + s * 0.08)
        path.lineTo(c.x() - s * 0.08, c.y() + s * 0.18)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "hold":
        p.drawLine(QPointF(c.x(), r.top() + s * 0.35), QPointF(c.x(), r.bottom()))
        p.drawEllipse(QPointF(c.x(), r.top() + s * 0.2), s * 0.17, s * 0.17)
    elif name == "tag":
        path = QPainterPath()
        path.moveTo(r.left(), r.top() + s * 0.1)
        path.lineTo(r.left() + s * 0.35, r.top() + s * 0.1)
        path.lineTo(r.right(), c.y() + s * 0.2)
        path.lineTo(c.x() + s * 0.1, r.bottom())
        path.lineTo(r.left(), r.top() + s * 0.45)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(r.left() + s * 0.15, r.top() + s * 0.25), s * 0.04, s * 0.04)
    elif name == "export":
        p.drawRoundedRect(QRectF(r.left(), c.y() - s * 0.05, r.width(), r.height() / 2 + s * 0.05), s * 0.06, s * 0.06)
        p.drawLine(QPointF(c.x(), r.top() - s * 0.02), QPointF(c.x(), c.y() + s * 0.12))
        p.drawLine(QPointF(c.x(), r.top() - s * 0.02), QPointF(c.x() - s * 0.12, r.top() + s * 0.1))
        p.drawLine(QPointF(c.x(), r.top() - s * 0.02), QPointF(c.x() + s * 0.12, r.top() + s * 0.1))
    elif name == "album":
        p.drawRoundedRect(QRectF(r.left() + s * 0.1, r.top(), r.width() - s * 0.1, r.height() - s * 0.1), s * 0.05, s * 0.05)
        p.drawLine(QPointF(r.left(), r.top() + s * 0.1), QPointF(r.left(), r.bottom()))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right() - s * 0.1, r.bottom()))
    elif name == "plus":
        p.drawLine(QPointF(c.x(), r.top() + s * 0.06), QPointF(c.x(), r.bottom() - s * 0.06))
        p.drawLine(QPointF(r.left() + s * 0.06, c.y()), QPointF(r.right() - s * 0.06, c.y()))
    elif name == "info":
        p.drawEllipse(r)
        p.drawLine(QPointF(c.x(), c.y() - s * 0.02), QPointF(c.x(), c.y() + s * 0.18))
        p.setBrush(color)
        p.drawEllipse(QPointF(c.x(), c.y() - s * 0.16), s * 0.035, s * 0.035)
    elif name == "people":
        p.drawEllipse(QPointF(c.x(), r.top() + s * 0.17), s * 0.15, s * 0.15)
        path = QPainterPath()
        path.moveTo(r.left() + s * 0.04, r.bottom())
        path.cubicTo(r.left() + s * 0.04, c.y() + s * 0.02, r.right() - s * 0.04, c.y() + s * 0.02,
                     r.right() - s * 0.04, r.bottom())
        p.drawPath(path)
    elif name == "pin":
        path = QPainterPath()
        path.moveTo(c.x(), r.bottom())
        path.cubicTo(c.x() - s * 0.42, c.y() - s * 0.05, c.x() - s * 0.22, r.top() - s * 0.08, c.x(), r.top() - s * 0.02)
        path.cubicTo(c.x() + s * 0.22, r.top() - s * 0.08, c.x() + s * 0.42, c.y() - s * 0.05, c.x(), r.bottom())
        p.drawPath(path)
        p.drawEllipse(QPointF(c.x(), c.y() - s * 0.1), s * 0.08, s * 0.08)
    elif name == "dupes":
        p.drawRoundedRect(QRectF(r.left(), r.top() + s * 0.16, r.width() * 0.7, r.height() * 0.7), s * 0.05, s * 0.05)
        p.drawRoundedRect(QRectF(r.left() + r.width() * 0.3, r.top(), r.width() * 0.7, r.height() * 0.7), s * 0.05, s * 0.05)
    elif name == "library":
        for i in range(2):
            for j in range(2):
                p.drawRoundedRect(QRectF(r.left() + i * r.width() * 0.55, r.top() + j * r.height() * 0.55,
                                         r.width() * 0.42, r.height() * 0.42), s * 0.04, s * 0.04)
    elif name == "edit":
        path = QPainterPath()
        path.moveTo(r.left() + s * 0.05, r.bottom() - s * 0.05)
        path.lineTo(r.left() + s * 0.1, r.bottom() - s * 0.22)
        path.lineTo(r.right() - s * 0.15, r.top() + s * 0.03)
        path.lineTo(r.right() - s * 0.03, r.top() + s * 0.15)
        path.lineTo(r.left() + s * 0.22, r.bottom() - s * 0.1)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "collage":
        p.drawRoundedRect(r, s * 0.05, s * 0.05)
        p.drawLine(QPointF(c.x() - s * 0.05, r.top()), QPointF(c.x() - s * 0.05, r.bottom()))
        p.drawLine(QPointF(c.x() - s * 0.05, c.y()), QPointF(r.right(), c.y()))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + s * 0.06)
        path.lineTo(r.left() + s * 0.25, r.top() + s * 0.06)
        path.lineTo(r.left() + s * 0.33, r.top() + s * 0.16)
        path.lineTo(r.right(), r.top() + s * 0.16)
        path.lineTo(r.right(), r.bottom())
        path.closeSubpath()
        p.drawPath(path)
    elif name == "video":
        p.drawRoundedRect(QRectF(r.left(), r.top() + s * 0.12, r.width() * 0.68, r.height() * 0.62), s * 0.06, s * 0.06)
        path = QPainterPath()
        path.moveTo(r.right() - s * 0.25, c.y())
        path.lineTo(r.right(), r.top() + s * 0.16)
        path.lineTo(r.right(), r.bottom() - s * 0.2)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "clock":
        p.drawEllipse(r)
        p.drawLine(c, QPointF(c.x(), r.top() + s * 0.14))
        p.drawLine(c, QPointF(c.x() + s * 0.14, c.y()))
    elif name == "eye-off":
        p.drawEllipse(QRectF(r.left(), c.y() - s * 0.16, r.width(), s * 0.32))
        p.drawLine(r.topLeft(), r.bottomRight())
    elif name == "back":
        p.drawLine(QPointF(r.right() - s * 0.15, r.top() + s * 0.05), QPointF(r.left() + s * 0.15, c.y()))
        p.drawLine(QPointF(r.left() + s * 0.15, c.y()), QPointF(r.right() - s * 0.15, r.bottom() - s * 0.05))
    elif name == "fwd":
        p.drawLine(QPointF(r.left() + s * 0.15, r.top() + s * 0.05), QPointF(r.right() - s * 0.15, c.y()))
        p.drawLine(QPointF(r.right() - s * 0.15, c.y()), QPointF(r.left() + s * 0.15, r.bottom() - s * 0.05))
    elif name == "sparkle":
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawPath(_star_path(c.x() - s * 0.05, c.y() + s * 0.05, s * 0.3, s * 0.07, 4))
        p.drawPath(_star_path(r.right() - s * 0.1, r.top() + s * 0.1, s * 0.13, s * 0.035, 4))
    elif name == "search":
        p.drawEllipse(QRectF(r.left(), r.top(), r.width() * 0.68, r.height() * 0.68))
        p.drawLine(QPointF(r.left() + r.width() * 0.58, r.top() + r.height() * 0.58), r.bottomRight())
    elif name == "settings":
        p.drawEllipse(c, s * 0.12, s * 0.12)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * s * 0.22, c.y() + math.sin(a) * s * 0.22),
                       QPointF(c.x() + math.cos(a) * s * 0.33, c.y() + math.sin(a) * s * 0.33))
        p.drawEllipse(c, s * 0.23, s * 0.23)
    else:
        p.drawEllipse(r)


_cache: dict = {}


def icon(name: str, color: QColor | None = None) -> QIcon:
    col = color or fg()
    key = (name, col.name())
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for size in (16, 32, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        _draw(name, p, size, col)
        p.end()
        ic.addPixmap(pm)
    _cache[key] = ic
    return ic
