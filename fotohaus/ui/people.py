"""People view: named people, face suggestions to confirm, and clusters of unnamed faces."""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QCompleter, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget, QAbstractItemView)

from .. import media
from ..paths import FACE_DIR

CROP_PX = 160


def face_crop_path(f) -> str:
    k = hashlib.sha1(f"{f['id']}|{f['path']}|{f['x']:.4f}{f['y']:.4f}".encode()).hexdigest()[:16]
    return str(FACE_DIR / f"{k}.jpg")


def make_face_crop(f) -> QImage:
    p = face_crop_path(f)
    img = QImage(p)
    if not img.isNull():
        return img
    im = media.load_image(f["path"], max_dim=1200)
    W, H = im.size
    cx, cy = (f["x"] + f["w"] / 2) * W, (f["y"] + f["h"] / 2) * H
    s = max(f["w"] * W, f["h"] * H) * 1.5
    box = (int(max(0, cx - s / 2)), int(max(0, cy - s / 2)), int(min(W, cx + s / 2)), int(min(H, cy + s / 2)))
    crop = im.crop(box)
    crop.thumbnail((CROP_PX, CROP_PX))
    FACE_DIR.mkdir(parents=True, exist_ok=True)
    crop.save(p, "JPEG", quality=88)
    return QImage(p)


def round_pixmap(img: QImage, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    if img.isNull():
        return pm
    src = QPixmap.fromImage(img).scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap((size - src.width()) // 2, (size - src.height()) // 2, src)
    p.end()
    return pm


class _Sig(QObject):
    ready = Signal(int, QImage)


class _CropTask(QRunnable):
    def __init__(self, sig, face):
        super().__init__()
        self.sig, self.face = sig, face

    def run(self):
        try:
            img = make_face_crop(self.face)
        except Exception:
            img = QImage()
        self.sig.ready.emit(self.face["id"], img)


class FaceCrops(QObject):
    ready = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.cache: dict[int, QImage] = {}
        self.pending: set[int] = set()
        self.sig = _Sig()
        self.sig.ready.connect(self._done)

    def get(self, face) -> QImage | None:
        img = self.cache.get(face["id"])
        if img is not None:
            return img
        if face["id"] not in self.pending:
            self.pending.add(face["id"])
            self.pool.start(_CropTask(self.sig, face))
        return None

    def _done(self, fid, img):
        self.pending.discard(fid)
        self.cache[fid] = img
        self.ready.emit(fid)


class ClusterRow(QWidget):
    named = Signal()

    def __init__(self, lib, crops, cluster, n, names, parent=None):
        super().__init__(parent)
        self.lib, self.crops, self.cluster = lib, crops, cluster
        self.faces = lib.faces_in_cluster(cluster, limit=200)
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 6, 4, 6)
        self.face_labels = {}
        for f in self.faces[:9]:
            lab = QLabel()
            lab.setFixedSize(64, 64)
            lab.setToolTip(f["path"])
            self.face_labels[f["id"]] = lab
            h.addWidget(lab)
            self._set(f)
        more = QLabel(f"{n} faces" if n > 9 else "")
        more.setObjectName("Muted")
        h.addWidget(more)
        h.addStretch()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Add a name")
        self.name.setFixedWidth(170)
        comp = QCompleter(names)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.name.setCompleter(comp)
        self.name.returnPressed.connect(self._name)
        h.addWidget(self.name)
        ok = QPushButton("Save")
        ok.clicked.connect(self._name)
        h.addWidget(ok)
        ig = QPushButton("Ignore")
        ig.setToolTip("Hide these faces (not a person / don't care)")
        ig.clicked.connect(self._ignore)
        h.addWidget(ig)
        crops.ready.connect(self._ready)

    def _set(self, f):
        img = self.crops.get(f)
        lab = self.face_labels.get(f["id"])
        if lab is not None and img is not None:
            lab.setPixmap(round_pixmap(img, 64))

    def _ready(self, fid):
        for f in self.faces[:9]:
            if f["id"] == fid:
                self._set(f)

    def _name(self):
        name = self.name.text().strip()
        if not name:
            return
        self.lib.name_faces([f["id"] for f in self.faces], name)
        self.named.emit()

    def _ignore(self):
        self.lib.ignore_faces([f["id"] for f in self.faces])
        self.named.emit()


class SuggestionsDialog(QDialog):
    def __init__(self, lib, crops, person_id, parent=None):
        super().__init__(parent)
        self.lib, self.crops, self.person_id = lib, crops, person_id
        p = lib.person(person_id)
        self.setWindowTitle(f"Is this {p['name']}?")
        self.resize(720, 520)
        v = QVBoxLayout(self)
        lab = QLabel("Unselect any face that is <b>not</b> this person, then click Confirm.")
        v.addWidget(lab)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(96, 96))
        self.list.setGridSize(QSize(112, 116))
        self.list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.list.setResizeMode(QListView.Adjust)
        v.addWidget(self.list)
        self.faces = lib.faces_for_person(person_id, confirmed=0)
        self.items = {}
        for f in self.faces:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, f["id"])
            it.setToolTip(f"{f['path']}\nmatch {f['suggestion'] or 0:.2f}")
            self.list.addItem(it)
            it.setSelected(True)
            self.items[f["id"]] = (it, f)
            img = crops.get(f)
            if img is not None:
                it.setIcon(QIcon(round_pixmap(img, 96)))
        crops.ready.connect(self._ready)
        bb = QDialogButtonBox()
        bb.addButton("Confirm", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self._confirm)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _ready(self, fid):
        if fid in self.items:
            it, f = self.items[fid]
            img = self.crops.cache.get(fid)
            if img is not None:
                it.setIcon(QIcon(round_pixmap(img, 96)))

    def _confirm(self):
        yes, no = [], []
        for fid, (it, _) in self.items.items():
            (yes if it.isSelected() else no).append(fid)
        if yes:
            self.lib.assign_faces(yes, self.person_id, True)
        if no:
            self.lib.reject_suggestions(no)
        self.accept()


class PeopleView(QWidget):
    person_selected = Signal(int)
    request_index = Signal()

    def __init__(self, lib, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.crops = FaceCrops(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        t = QLabel("People")
        t.setObjectName("Title")
        top.addWidget(t)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        top.addWidget(self.status)
        top.addStretch()
        scan = QPushButton("Find Faces Now")
        scan.clicked.connect(self.request_index)
        top.addWidget(scan)
        v.addLayout(top)
        self.people = QListWidget()
        self.people.setViewMode(QListView.IconMode)
        self.people.setIconSize(QSize(96, 96))
        self.people.setGridSize(QSize(132, 150))
        self.people.setResizeMode(QListView.Adjust)
        self.people.setMovement(QListView.Static)
        self.people.setWordWrap(True)
        self.people.setMaximumHeight(330)
        self.people.itemDoubleClicked.connect(lambda it: self.person_selected.emit(it.data(Qt.UserRole)))
        self.people.setContextMenuPolicy(Qt.CustomContextMenu)
        self.people.customContextMenuRequested.connect(self._ctx)
        v.addWidget(self.people)
        self.sugg_lbl = QLabel()
        self.sugg_lbl.setObjectName("Muted")
        v.addWidget(self.sugg_lbl)
        l2 = QLabel("UNNAMED")
        l2.setObjectName("Section")
        v.addWidget(l2)
        self.clusters_area = QScrollArea()
        self.clusters_area.setWidgetResizable(True)
        self.clusters_area.setFrameShape(QScrollArea.NoFrame)
        v.addWidget(self.clusters_area, 1)
        self.crops.ready.connect(self._crop_ready)
        self._person_items: dict[int, tuple] = {}

    def refresh(self):
        self.people.clear()
        self._person_items.clear()
        total_sugg = 0
        for p in self.lib.people():
            if p["hidden"]:
                continue
            faces = self.lib.faces_for_person(p["id"], confirmed=1, limit=1)
            if not faces and not p["suggestions"]:
                continue
            label = f"{p['name']}\n{p['n']} photo{'s' if p['n'] != 1 else ''}"
            if p["suggestions"]:
                label += f"\n{p['suggestions']} to confirm"
                total_sugg += p["suggestions"]
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, p["id"])
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.people.addItem(it)
            if faces:
                self._person_items[faces[0]["id"]] = (it, faces[0])
                img = self.crops.get(faces[0])
                if img is not None:
                    it.setIcon(QIcon(round_pixmap(img, 96)))
        self.sugg_lbl.setText(f"{total_sugg} suggested matches to review — right-click a person and choose "
                              f"“Review suggestions”." if total_sugg else "")
        names = [p["name"] for p in self.lib.people()]
        w = QWidget()
        cv = QVBoxLayout(w)
        cv.setContentsMargins(0, 0, 0, 0)
        clusters = self.lib.unnamed_clusters(min_size=1, limit=40)
        for c in clusters:
            row = ClusterRow(self.lib, self.crops, c["cluster"], c["n"], names)
            row.named.connect(self.refresh)
            cv.addWidget(row)
        if not clusters:
            n_faces = self.lib.db.scalar("SELECT COUNT(*) FROM faces", default=0)
            msg = ("No unnamed faces. " if n_faces else
                   "No faces found yet. Fotohaus looks for faces in the background after photos are added.")
            lab = QLabel(msg)
            lab.setObjectName("Muted")
            lab.setWordWrap(True)
            cv.addWidget(lab)
        cv.addStretch()
        self.clusters_area.setWidget(w)
        c = self.lib.counts()
        self.status.setText(f"   {c['people']} people · {c['faces']} faces")

    def _crop_ready(self, fid):
        if fid in self._person_items:
            it, f = self._person_items[fid]
            img = self.crops.cache.get(fid)
            if img is not None:
                it.setIcon(QIcon(round_pixmap(img, 96)))

    def _ctx(self, pos):
        it = self.people.itemAt(pos)
        if not it:
            return
        pid = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Show Photos", lambda: self.person_selected.emit(pid))
        m.addAction("Review Suggestions…", lambda: self._review(pid))
        m.addAction("Rename…", lambda: self._rename(pid))
        m.addAction("Merge Into…", lambda: self._merge(pid))
        m.addSeparator()
        m.addAction("Remove Person", lambda: self._remove(pid))
        m.exec(self.people.viewport().mapToGlobal(pos))

    def _review(self, pid):
        if SuggestionsDialog(self.lib, self.crops, pid, self).exec():
            self.refresh()

    def _rename(self, pid):
        p = self.lib.person(pid)
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=p["name"])
        if ok and name.strip():
            self.lib.rename_person(pid, name.strip())
            self.refresh()

    def _merge(self, pid):
        others = [p for p in self.lib.people() if p["id"] != pid]
        names = [p["name"] for p in others]
        if not names:
            return
        name, ok = QInputDialog.getItem(self, "Merge", "Merge into:", names, 0, False)
        if ok:
            dst = next(p["id"] for p in others if p["name"] == name)
            self.lib.merge_people(pid, dst)
            self.refresh()

    def _remove(self, pid):
        if QMessageBox.question(self, "Remove person",
                                "Remove this person? Their faces become unnamed again.") == QMessageBox.Yes:
            self.lib.remove_person(pid)
            self.refresh()
