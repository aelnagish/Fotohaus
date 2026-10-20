"""Places view: a lightweight OpenStreetMap slippy map with photo clusters and geotagging."""
from __future__ import annotations

import json
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QUrl, QUrlQuery, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..paths import TILE_DIR
from . import theme

TILE = 256
UA = b"Fotohaus/1.0 (desktop photo organizer)"
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def lonlat_to_world(lon, lat, z):
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat = max(-85.0511, min(85.0511, lat))
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def world_to_lonlat(x, y, z):
    n = TILE * (2 ** z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


class MapWidget(QWidget):
    cluster_clicked = Signal(list)          # photo ids
    location_picked = Signal(float, float)  # lat, lon

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.z = 2
        self.center = (10.0, 30.0)  # lon, lat
        self.points: list = []      # rows with id, lat, lon
        self.tiles: dict = {}
        self.inflight: set = set()
        self.enabled_tiles = True
        self.pick_mode = False
        self._drag = None
        self._clusters: list = []
        self.nam = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self)
        cache.setCacheDirectory(str(TILE_DIR))
        cache.setMaximumCacheSize(400 * 1024 * 1024)
        self.nam.setCache(cache)
        self.nam.finished.connect(self._tile_done)
        self.setMouseTracking(True)
        self.setMinimumSize(300, 200)
        loader.loaded.connect(lambda _: self.update())

    def set_points(self, rows):
        self.points = list(rows)
        self.update()

    def fit_points(self):
        if not self.points:
            return
        lats = [r["lat"] for r in self.points]
        lons = [r["lon"] for r in self.points]
        self.center = ((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)
        for z in range(17, 1, -1):
            x0, y0 = lonlat_to_world(min(lons), max(lats), z)
            x1, y1 = lonlat_to_world(max(lons), min(lats), z)
            if x1 - x0 < self.width() * 0.8 and y1 - y0 < self.height() * 0.8:
                self.z = z
                break
        else:
            self.z = 2
        self.update()

    def go(self, lat, lon, z=13):
        self.center = (lon, lat)
        self.z = z
        self.update()

    # -- tiles
    def _tile(self, z, x, y):
        key = (z, x, y)
        pm = self.tiles.get(key)
        if pm is not None:
            return pm
        if self.enabled_tiles and key not in self.inflight and len(self.inflight) < 24:
            self.inflight.add(key)
            req = QNetworkRequest(QUrl(TILE_URL.format(z=z, x=x, y=y)))
            req.setRawHeader(b"User-Agent", UA)
            req.setAttribute(QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.PreferCache)
            reply = self.nam.get(req)
            reply.setProperty("tile", json.dumps(key))
        return None

    def _tile_done(self, reply: QNetworkReply):
        key_s = reply.property("tile")
        if key_s is None:
            return
        key = tuple(json.loads(key_s))
        self.inflight.discard(key)
        if reply.error() == QNetworkReply.NoError:
            pm = QPixmap()
            if pm.loadFromData(bytes(reply.readAll())):
                self.tiles[key] = pm
                if len(self.tiles) > 600:
                    for k in list(self.tiles)[:200]:
                        del self.tiles[k]
                self.update()
        reply.deleteLater()

    # -- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        dark = theme.is_dark()
        p.fillRect(self.rect(), QColor("#2a3138") if dark else QColor("#dfe7ee"))
        cx, cy = lonlat_to_world(*self.center, self.z)
        ox, oy = cx - self.width() / 2, cy - self.height() / 2
        n = 2 ** self.z
        tx0, ty0 = int(ox // TILE), int(oy // TILE)
        tx1, ty1 = int((ox + self.width()) // TILE), int((oy + self.height()) // TILE)
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                if ty < 0 or ty >= n:
                    continue
                pm = self._tile(self.z, tx % n, ty)
                target = QRectF(tx * TILE - ox, ty * TILE - oy, TILE, TILE)
                if pm is not None:
                    p.drawPixmap(target.toRect(), pm)
                else:
                    p.setPen(QPen(QColor(0, 0, 0, 20), 1))
                    p.drawRect(target)
        # clusters
        cell = 64
        buckets: dict = {}
        for r in self.points:
            x, y = lonlat_to_world(r["lon"], r["lat"], self.z)
            sx, sy = x - ox, y - oy
            if -cell < sx < self.width() + cell and -cell < sy < self.height() + cell:
                k = (int(sx // cell), int(sy // cell))
                b = buckets.setdefault(k, [0.0, 0.0, []])
                b[0] += sx
                b[1] += sy
                b[2].append(r)
        self._clusters = []
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        for (_, _), (sx, sy, rows) in buckets.items():
            n_ = len(rows)
            c = QPointF(sx / n_, sy / n_)
            size = 52
            rect = QRectF(c.x() - size / 2, c.y() - size - 8, size, size)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(rect.translated(1, 2), 6, 6)
            p.setBrush(Qt.white)
            p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 7, 7)
            pm = self.loader.get(rows[0])
            if pm is not None:
                src = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                p.drawPixmap(rect.toRect(), src, QRectF((src.width() - size) / 2, (src.height() - size) / 2,
                                                       size, size).toRect())
            # pointer
            p.setBrush(Qt.white)
            p.drawPolygon(QPolygonF([QPointF(c.x() - 6, rect.bottom() + 2), QPointF(c.x() + 6, rect.bottom() + 2),
                                     QPointF(c.x(), rect.bottom() + 9)]))
            if n_ > 1:
                badge = QRectF(rect.right() - 12, rect.top() - 10, 26, 18)
                p.setBrush(theme.ACCENT)
                p.drawRoundedRect(badge, 9, 9)
                p.setPen(Qt.white)
                p.drawText(badge, Qt.AlignCenter, str(n_) if n_ < 1000 else "999+")
                p.setPen(Qt.NoPen)
            self._clusters.append((rect.adjusted(-4, -12, 14, 10), [r["id"] for r in rows]))
        # attribution
        f2 = theme.scaled_font(self.font(), 0.8)
        p.setFont(f2)
        txt = "© OpenStreetMap contributors" if self.enabled_tiles else "Map tiles are turned off in Settings"
        fm = p.fontMetrics()
        ar = QRectF(self.width() - fm.horizontalAdvance(txt) - 14, self.height() - 20, fm.horizontalAdvance(txt) + 10, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 190))
        p.drawRect(ar)
        p.setPen(QColor(40, 40, 40))
        p.drawText(ar, Qt.AlignCenter, txt)
        if self.pick_mode:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            br = QRectF(self.width() / 2 - 190, 12, 380, 28)
            p.drawRoundedRect(br, 14, 14)
            p.setPen(Qt.white)
            p.drawText(br, Qt.AlignCenter, "Click the map to place the selected photos")

    # -- interaction
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.position(), self.center, False)

    def mouseMoveEvent(self, e):
        if self._drag:
            start, center, _ = self._drag
            d = e.position() - start
            if d.manhattanLength() > 3:
                cx, cy = lonlat_to_world(*center, self.z)
                self.center = world_to_lonlat(cx - d.x(), cy - d.y(), self.z)
                self._drag = (start, center, True)
                self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and not self._drag[2]:
            pos = e.position()
            if self.pick_mode:
                cx, cy = lonlat_to_world(*self.center, self.z)
                lon, lat = world_to_lonlat(cx - self.width() / 2 + pos.x(), cy - self.height() / 2 + pos.y(), self.z)
                self.pick_mode = False
                self.location_picked.emit(lat, lon)
                self.update()
            else:
                for rect, ids in reversed(self._clusters):
                    if rect.contains(pos):
                        self.cluster_clicked.emit(ids)
                        break
        self._drag = None

    def wheelEvent(self, e):
        if not e.pixelDelta().isNull() and not (e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            # trackpad: two-finger scroll pans the map (pinch zooms, see event())
            d = e.pixelDelta()
            cx, cy = lonlat_to_world(*self.center, self.z)
            self.center = world_to_lonlat(cx - d.x(), cy - d.y(), self.z)
            self.update()
            return
        # mouse wheels send 120 per notch; smooth wheels send small steps that add up
        self._wheel_acc = getattr(self, "_wheel_acc", 0.0) + e.angleDelta().y() / 120.0
        if abs(self._wheel_acc) < 0.5:
            return
        steps, self._wheel_acc = self._wheel_acc, 0.0
        self._zoom_at(e.position(), 1 if steps > 0 else -1)

    def event(self, e):
        if e.type() == QEvent.NativeGesture and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self._pinch = getattr(self, "_pinch", 0.0) + e.value()
            if abs(self._pinch) >= 0.35:
                self._zoom_at(e.position(), 1 if self._pinch > 0 else -1)
                self._pinch = 0.0
            return True
        return super().event(e)

    def _zoom_at(self, pos, d):
        cx, cy = lonlat_to_world(*self.center, self.z)
        mx, my = cx - self.width() / 2 + pos.x(), cy - self.height() / 2 + pos.y()
        mlon, mlat = world_to_lonlat(mx, my, self.z)
        nz = max(1, min(18, self.z + d))
        if nz == self.z:
            return
        self.z = nz
        nx, ny = lonlat_to_world(mlon, mlat, self.z)
        self.center = world_to_lonlat(nx - pos.x() + self.width() / 2, ny - pos.y() + self.height() / 2, self.z)
        self.update()

    def mouseDoubleClickEvent(self, e):
        self._zoom_at(e.position(), 1)


class PlacesView(QWidget):
    show_photos = Signal(list)

    def __init__(self, lib, loader, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.selected_ids: list[int] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("EditorBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        t = QLabel("Places")
        t.setObjectName("Title")
        h.addWidget(t)
        self.info = QLabel()
        self.info.setObjectName("Muted")
        h.addWidget(self.info)
        h.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a place…")
        self.search.setFixedWidth(220)
        self.search.returnPressed.connect(self._geocode)
        h.addWidget(self.search)
        self.place_btn = QPushButton(theme.icon("pin"), " Place Selected Photos")
        self.place_btn.setToolTip("Select photos in the library first, then click here and click the map")
        self.place_btn.clicked.connect(self._start_pick)
        h.addWidget(self.place_btn)
        fit = QPushButton("Show All")
        fit.clicked.connect(lambda: self.map.fit_points())
        h.addWidget(fit)
        v.addWidget(bar)
        self.map = MapWidget(loader)
        self.map.cluster_clicked.connect(self.show_photos)
        self.map.location_picked.connect(self._picked)
        v.addWidget(self.map, 1)
        self.nam = QNetworkAccessManager(self)
        self.nam.finished.connect(self._geo_done)
        self._fitted = False

    def refresh(self, fit=False):
        self.map.enabled_tiles = bool(self.lib.settings.get("map_tiles"))
        rows = self.lib.geotagged()
        self.map.set_points(rows)
        n_unplaced = self.lib.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE lat IS NULL AND hidden=0 AND {self.lib.vis()}", default=0)
        self.info.setText(f"   {len(rows)} photos on the map · {n_unplaced} without a location")
        if fit or not self._fitted:
            self.map.fit_points()
            self._fitted = True
        self.place_btn.setEnabled(bool(self.selected_ids))
        self.place_btn.setText(f" Place {len(self.selected_ids)} Selected" if self.selected_ids
                               else " Place Selected Photos")

    def set_selection(self, ids):
        self.selected_ids = list(ids)
        self.place_btn.setEnabled(bool(ids))
        self.place_btn.setText(f" Place {len(ids)} Selected" if ids else " Place Selected Photos")

    def _start_pick(self):
        if self.selected_ids:
            self.map.pick_mode = True
            self.map.update()

    def _picked(self, lat, lon):
        self.lib.set_location(self.selected_ids, lat, lon)
        self.refresh()

    def _geocode(self):
        q = self.search.text().strip()
        if not q:
            return
        url = QUrl("https://nominatim.openstreetmap.org/search")
        qq = QUrlQuery()
        qq.addQueryItem("q", q)
        qq.addQueryItem("format", "json")
        qq.addQueryItem("limit", "1")
        url.setQuery(qq)
        req = QNetworkRequest(url)
        req.setRawHeader(b"User-Agent", UA)
        self.nam.get(req)

    def _geo_done(self, reply):
        try:
            data = json.loads(bytes(reply.readAll()).decode() or "[]")
            if data:
                lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                self.map.go(lat, lon, 12)
            else:
                self.info.setText("   Place not found")
        except Exception:
            self.info.setText("   Couldn't reach the place search service")
        reply.deleteLater()
