"""Colors, stylesheet and vector icons (drawn in code — no third-party icon assets)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPainterPath, QPalette, QPen,
                           QPixmap)

ACCENT = QColor("#2f7de1")          # mutated in place by apply_appearance(), so every user sees the change
STAR = QColor("#f5b301")
DEFAULT_ACCENT = "#2f7de1"

ACCENTS = [("system", "Match System", None), ("blue", "Blue", "#2f7de1"), ("purple", "Purple", "#7f5bd6"),
           ("pink", "Pink", "#d6458f"), ("red", "Red", "#e0453f"), ("orange", "Orange", "#f08a24"),
           ("yellow", "Yellow", "#e0b21b"), ("green", "Green", "#3fa34d"), ("graphite", "Graphite", "#8a8b90")]

_APPEARANCE = {"theme": "system", "accent": "system", "library_bg": "neutral"}


def system_accent() -> QColor:
    """The Mac's accent colour (System Settings ▸ Appearance), as Qt reports it."""
    try:
        from PySide6.QtWidgets import QApplication
        style = QApplication.style()
        pal = style.standardPalette() if style else QGuiApplication.palette()
        c = pal.color(QPalette.Accent) if hasattr(QPalette, "Accent") else pal.color(QPalette.Highlight)
        if c.isValid() and c.saturation() > 25:
            return c
    except Exception:
        pass
    return QColor(DEFAULT_ACCENT)


def apply_appearance(app, theme: str | None = None, accent: str | None = None,
                     library_bg: str | None = None) -> None:
    """Apply Light/Dark/Match System, the accent colour and the library background to the whole app."""
    if theme is not None:
        _APPEARANCE["theme"] = theme
    if accent is not None:
        _APPEARANCE["accent"] = accent
    if library_bg is not None:
        _APPEARANCE["library_bg"] = library_bg
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):
        scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(_APPEARANCE["theme"],
                                                                                   Qt.ColorScheme.Unknown)
        hints.setColorScheme(scheme)
        if not getattr(app, "_fh_scheme_hooked", False):
            hints.colorSchemeChanged.connect(lambda *_: QTimer.singleShot(0, lambda: _restyle(app)))
            app._fh_scheme_hooked = True
    _restyle(app)


def _fallback_palette(dark: bool) -> QPalette:
    """Used only where the platform style can't switch Light/Dark by itself (macOS can)."""
    pal = QPalette()
    if dark:
        base, alt, text, win, btn = "#1e1f22", "#26272a", "#e8e8ea", "#232427", "#2e2f33"
    else:
        base, alt, text, win, btn = "#ffffff", "#f4f5f7", "#1f2124", "#ececee", "#f6f6f7"
    for role, col in ((QPalette.Window, win), (QPalette.WindowText, text), (QPalette.Base, base),
                      (QPalette.AlternateBase, alt), (QPalette.Text, text), (QPalette.Button, btn),
                      (QPalette.ButtonText, text), (QPalette.ToolTipBase, alt), (QPalette.ToolTipText, text),
                      (QPalette.PlaceholderText, "#8c8f96")):
        pal.setColor(role, QColor(col))
    return pal


def _accent_images() -> dict:
    """Checkbox / radio / drop-down arrow images in the current accent (stylesheets need image files)."""
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "fotohaus-ui"
    d.mkdir(exist_ok=True)
    dark = is_dark()
    tag = f"{ACCENT.name()[1:]}_{int(dark)}"
    out = {}
    off_border = QColor("#6a6c72" if dark else "#b3b6bd")
    off_fill = QColor("#2a2b2f" if dark else "#ffffff")
    for kind in ("check", "radio"):
        for on in (False, True):
            for enabled in (True, False):
                pm = QPixmap(36, 36)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.Antialiasing)
                if not enabled:
                    p.setOpacity(0.45)
                r = QRectF(3, 3, 30, 30)
                if on:
                    p.setPen(Qt.NoPen)
                    p.setBrush(ACCENT)
                else:
                    p.setPen(QPen(off_border, 2))
                    p.setBrush(off_fill)
                (p.drawRoundedRect(r, 8, 8) if kind == "check" else p.drawEllipse(r))
                if on:
                    p.setPen(QPen(QColor("white"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    if kind == "check":
                        p.drawPolyline([QPointF(10, 18.5), QPointF(15.5, 24), QPointF(26, 12)])
                    else:
                        p.setPen(Qt.NoPen)
                        p.setBrush(QColor("white"))
                        p.drawEllipse(QRectF(12.5, 12.5, 11, 11))
                p.end()
                f = d / f"{kind}_{int(on)}_{int(enabled)}_{tag}.png"
                pm.save(str(f))
                out[(kind, on, enabled)] = f.as_posix()
    pm = QPixmap(36, 36)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(ACCENT)
    p.drawRoundedRect(QRectF(2, 2, 32, 32), 8, 8)
    p.setPen(QPen(QColor("white"), 3.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline([QPointF(12, 15), QPointF(18, 9.5), QPointF(24, 15)])
    p.drawPolyline([QPointF(12, 21), QPointF(18, 26.5), QPointF(24, 21)])
    p.end()
    f = d / f"combo_{tag}.png"
    pm.save(str(f))
    out["combo"] = f.as_posix()
    return out


def _accent_controls_css() -> str:
    """macOS draws checkboxes, default buttons, drop-downs and sliders in the *system* accent.
    When the user picks another accent, draw those controls ourselves so every accent matches."""
    if _APPEARANCE.get("accent", "system") == "system":
        return ""
    dark = is_dark()
    im = _accent_images()
    border = "#4a4b50" if dark else "#c9ccd2"
    field = "#2e2f33" if dark else "#ffffff"
    txt = "#e8e8ea" if dark else "#1f2124"
    a = ACCENT.name()
    css = f"""
    QPushButton:default {{ background: {a}; color: white; border: none; border-radius: 6px;
                          padding: 4px 18px; min-height: 18px; }}
    QPushButton:default:pressed {{ background: {ACCENT.darker(115).name()}; }}
    QComboBox {{ border: 1px solid {border}; border-radius: 6px; padding: 3px 30px 3px 9px;
                 background: {field}; color: {txt}; min-height: 18px; }}
    QComboBox:disabled {{ color: #8c8f96; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 20px;
                            margin-right: 3px; border: none; }}
    QComboBox::down-arrow {{ image: url({im['combo']}); width: 17px; height: 17px; }}
    QComboBox QAbstractItemView {{ selection-background-color: {a}; selection-color: white; }}
    QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: white; border: 1px solid {border}; width: 16px; height: 16px;
                                  margin: -7px 0; border-radius: 8px; }}
    QProgressBar::chunk {{ background: {a}; border-radius: 3px; }}
    """
    for kind, sel in (("check", "QCheckBox"), ("radio", "QRadioButton")):
        css += f"""
    {sel}::indicator {{ width: 16px; height: 16px; }}
    {sel}::indicator:unchecked {{ image: url({im[(kind, False, True)]}); }}
    {sel}::indicator:checked {{ image: url({im[(kind, True, True)]}); }}
    {sel}::indicator:unchecked:disabled {{ image: url({im[(kind, False, False)]}); }}
    {sel}::indicator:checked:disabled {{ image: url({im[(kind, True, False)]}); }}"""
    return css


def _restyle(app) -> None:
    import sys
    want_theme = _APPEARANCE["theme"]
    # macOS switches Light/Dark natively through setColorScheme(); a hand-made palette there would
    # override the native controls (bordered tool buttons, grey dialogs), so only use it elsewhere.
    if sys.platform != "darwin" and want_theme in ("light", "dark") and is_dark() != (want_theme == "dark"):
        app.setPalette(_fallback_palette(want_theme == "dark"))
        app._fh_fallback_palette = True
    elif getattr(app, "_fh_fallback_palette", False) and (sys.platform == "darwin" or want_theme == "system"):
        app.setPalette(app.style().standardPalette())
        app._fh_fallback_palette = False
    hexes = dict((k, h) for k, _n, h in ACCENTS)
    want = hexes.get(_APPEARANCE["accent"])
    col = QColor(want) if want else system_accent()
    ACCENT.setRgb(col.red(), col.green(), col.blue())
    pal = app.palette()
    for role in (QPalette.Highlight,) + ((QPalette.Accent,) if hasattr(QPalette, "Accent") else ()):
        pal.setColor(role, ACCENT)
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(pal)
    _cache.clear()
    app.setStyleSheet(stylesheet() + _accent_controls_css())
    for w in app.allWidgets():
        w.update()


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
    if _APPEARANCE.get("library_bg") == "gallery":
        grid_bg = "#111213" if dark else "#e9ebee"      # a quieter backdrop that lets photos stand out
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
    QFrame#FolderList {{ background: {card_bg}; border: 1px solid {border}; border-radius: 8px; }}
    QFrame#FolderList QLabel {{ background: transparent; }}
    QLabel#FolderName {{ font-weight: 600; }}
    QLabel#OptHeading {{ color: {sec}; font-size: 11px; font-weight: 600; padding-top: 4px; }}
    QFrame#OptSep {{ color: {border}; background: {border}; max-height: 1px; border: none; margin: 4px 0; }}
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


class _GlyphEngine(QIconEngine):
    """Draws a glyph on demand, so icons follow theme and accent changes without being rebuilt."""

    def __init__(self, name, color, selected=None):
        super().__init__()
        self.name, self.color, self.selected = name, color, selected

    def paint(self, painter, rect, mode, state):
        if mode == QIcon.Selected and self.selected is not None:
            col = QColor(self.selected)
        else:
            col = QColor(self.color) if self.color is not None else fg()
        if mode == QIcon.Disabled:
            col.setAlpha(110)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(rect.x(), rect.y())
        side = min(rect.width(), rect.height())
        _draw(self.name, painter, side, col)
        painter.restore()

    def pixmap(self, size, mode, state):
        pm = QPixmap(size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        self.paint(p, pm.rect(), mode, state)
        p.end()
        return pm

    def clone(self):
        return _GlyphEngine(self.name, self.color, self.selected)


def icon(name: str, color: QColor | None = None, selected: QColor | None = None) -> QIcon:
    """A vector icon. With no colour it uses the current text colour (and updates with the theme);
    pass theme.ACCENT to follow the accent colour. `selected` is the colour on a selected row."""
    live = color is None or color is ACCENT or color is STAR
    key = (name, None if color is None else (id(color) if live else color.name()),
           None if selected is None else QColor(selected).name())
    ic = _cache.get(key)
    if ic is None:
        ic = QIcon(_GlyphEngine(name, color if live else QColor(color), selected))
        _cache[key] = ic
    return ic
