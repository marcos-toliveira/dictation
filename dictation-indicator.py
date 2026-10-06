#!/usr/bin/env python3
# ==============================================================================
# dictation-indicator — overlay "REC" flutuante, ancorado na janela em foco
#
# Janela frameless, always-on-top, translúcida, CLICK-THROUGH e sem roubar foco.
# Mostra um ponto vermelho pulsante + "REC" perto da caixa de texto em foco
# (canto inferior-direito da janela ativa; configurável via --anchor).
#
# Roda até receber SIGTERM (o `dictation stop` mata). Sem dependências além do
# PySide6 e do xdotool (já presentes).
# ==============================================================================
import math
import signal
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QWidget

ANCHOR = sys.argv[1] if len(sys.argv) > 1 else "bottom-right"


def screen_at(x, y):
    for s in QApplication.screens():
        if s.geometry().contains(x, y):
            return s
    return QApplication.primaryScreen()


def active_geometry():
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
            capture_output=True, text=True, timeout=2,
        ).stdout
        d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        return int(d["X"]), int(d["Y"]), int(d["WIDTH"]), int(d["HEIGHT"])
    except Exception:
        return None


def mouse_pos():
    try:
        out = subprocess.run(["xdotool", "getmouselocation", "--shell"],
                             capture_output=True, text=True, timeout=2).stdout
        d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        return int(d["X"]), int(d["Y"])
    except Exception:
        return None


class Indicator(QWidget):
    W, H = 74, 38

    def __init__(self):
        super().__init__()
        self.phase = 0.0
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("dictation-rec")
        self.resize(self.W, self.H)
        QTimer(self, timeout=self._tick, interval=60).start()
        QTimer(self, timeout=self.reposition, interval=700).start()
        self.reposition()

    def _tick(self):
        self.phase += 0.14
        self.update()

    def reposition(self):
        # monitor que contém o ponto (x,y); fallback: primário
        def screen_at(x, y):
            for s in QApplication.screens():
                if s.geometry().contains(x, y):
                    return s
            return QApplication.primaryScreen()

        g = active_geometry()
        if ANCHOR == "mouse":
            m = mouse_pos()
            if m:
                px, py = m[0] + 16, m[1] + 16
                scr = screen_at(m[0], m[1]).availableGeometry()
            else:
                scr = QApplication.primaryScreen().availableGeometry()
                px, py = scr.right() - self.W - 40, scr.top() + 40
        elif g:
            x, y, w, h = g
            if ANCHOR == "top-right":
                px, py = x + w - self.W - 18, y + 18
            elif ANCHOR == "bottom-center":
                px, py = x + w // 2 - self.W // 2, y + h - self.H - 18
            elif ANCHOR == "top-left":
                px, py = x + 18, y + 18
            else:  # bottom-right
                px, py = x + w - self.W - 18, y + h - self.H - 18
            scr = screen_at(x + w // 2, y + h // 2).availableGeometry()
        else:
            scr = QApplication.primaryScreen().availableGeometry()
            px, py = scr.right() - self.W - 40, scr.top() + 40
        px = max(scr.left(), min(px, scr.right() - self.W))
        py = max(scr.top(), min(py, scr.bottom() - self.H))
        self.move(px, py)
        if self.isVisible():
            self.raise_()
        try:
            (Path.home() / ".local" / "state" / "dictation" / "indicator.geom").write_text(
                f"{px},{py},{self.W},{self.H},active={g},avail={scr.getRect()}")
        except OSError:
            pass

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 18, 18, 200))
        p.drawRoundedRect(QRectF(0, 0, self.W, self.H), 10, 10)
        s = 0.5 + 0.5 * math.sin(self.phase)
        cy = self.H / 2
        rad = 5 + 3 * s
        p.setBrush(QColor(235, 45, 45, int(180 + 75 * s)))
        p.drawEllipse(QPointF(17, cy), rad, rad)
        p.setPen(QColor(255, 255, 255, 230))
        f = QFont()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(30, 0, self.W - 32, self.H), Qt.AlignVCenter | Qt.AlignLeft, "REC")


class Preview(QWidget):
    """Bolha flutuante com o texto parcial (prévia ao vivo durante a fala)."""

    MAXW = 680
    PAD = 12

    def __init__(self, anchor: str = "bottom-center"):
        super().__init__()
        self.anchor = anchor
        self._text = ""
        self._font = QFont()
        self._font.setPointSize(10)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("dictation-preview")
        self.resize(self.MAXW, 40)
        QTimer(self, timeout=self.follow, interval=500).start()
        self.hide()

    def set_text(self, text: str) -> None:
        self._text = (text or "").strip()
        if not self._text:
            self.hide()
            return
        self._resize()
        self.show()
        self.follow()
        self.raise_()

    def _resize(self) -> None:
        fm = QFontMetrics(self._font)
        w = min(self.MAXW, max(220, fm.horizontalAdvance(self._text) + 2 * self.PAD + 4))
        rect = fm.boundingRect(0, 0, w - 2 * self.PAD, 100000, Qt.TextWordWrap, self._text)
        self.resize(w, rect.height() + 2 * self.PAD)

    def follow(self) -> None:
        if not self.isVisible():
            return
        scr = None
        g = active_geometry()
        if self.anchor == "mouse":
            m = mouse_pos()
            if m:
                px, py = m[0] - self.width() // 2, m[1] - self.height() - 24
                scr = screen_at(m[0], m[1]).availableGeometry()
        if scr is None and g:
            x, y, w, h = g
            if self.anchor == "top-right":
                px, py = x + w - self.width() - 18, y + 18
            elif self.anchor == "top-left":
                px, py = x + 18, y + 18
            elif self.anchor == "bottom-right":
                px, py = x + w - self.width() - 18, y + h - self.height() - 64
            else:  # bottom-center
                px, py = x + w // 2 - self.width() // 2, y + h - self.height() - 64
            scr = screen_at(x + w // 2, y + h // 2).availableGeometry()
        if scr is None:
            scr = QApplication.primaryScreen().availableGeometry()
            px, py = scr.width() // 2 - self.width() // 2, scr.bottom() - self.height() - 40
        px = max(scr.left(), min(px, scr.right() - self.width()))
        py = max(scr.top(), min(py, scr.bottom() - self.height()))
        self.move(px, py)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(15, 15, 15, 215))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        p.setPen(QColor(240, 240, 240, 240))
        p.setFont(self._font)
        p.drawText(QRectF(self.PAD, self.PAD, self.width() - 2 * self.PAD, self.height() - 2 * self.PAD),
                   Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter, self._text)


def main() -> int:
    app = QApplication(sys.argv[:1])
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    ind = Indicator()
    ind.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
