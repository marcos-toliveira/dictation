#!/usr/bin/env python3
# ==============================================================================
# dictationd — daemon quente do dictation
#
# Mantém o indicador (Qt) pronto e responde a comandos por um socket local, para
# que a captura comece ~50ms depois do atalho (sem esperar o boot do Python/Qt).
#
# Comandos (uma linha): toggle | start | stop | status | quit
# Socket: ${XDG_RUNTIME_DIR:-/tmp}/dictation.sock
#
# Reusa a lógica de ASR/injeção de ~/.local/bin/dictation (importlib).
# ==============================================================================
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMenu,
    QSystemTrayIcon,
)

HOME = Path.home()
BIN = HOME / ".local" / "bin"


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


core = _load(BIN / "dictation", "dictation_core")
indmod = _load(BIN / "dictation-indicator", "dictation_indicator")


def sock_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "dictation.sock")


class Daemon(QObject):
    previewReady = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.recording = False
        self.proc: subprocess.Popen | None = None
        self.t0 = 0.0
        self._dlg = None
        self._dlg_wrong = None
        self._dlg_right = None
        self._dlg_vocab = None
        self._gen = 0
        self._preview_busy = False
        self._last_raw = 0
        self._preview_on = bool(cfg["preview"]) and cfg["provider"] == "groq"
        self.ind = indmod.Indicator()
        indmod.ANCHOR = cfg["indicator_anchor"]
        self.ind.hide()
        self.preview = indmod.Preview(cfg["preview_anchor"])
        self.previewReady.connect(self.preview.set_text)
        self._ptimer = QTimer(self, timeout=self._preview_tick, interval=cfg["preview_interval_ms"])
        self._build_tray()
        QTimer(self.ind, timeout=self._watchdog, interval=1000).start()

    def _build_tray(self) -> None:
        """Ícone na bandeja: indica que o daemon está de pé (e o estado)."""
        self.tray = QSystemTrayIcon(QIcon.fromTheme("audio-input-microphone"), self.ind)
        menu = QMenu()
        menu.addAction("Iniciar / parar").triggered.connect(lambda: self.handle("toggle"))
        menu.addAction("Ensinar correção…").triggered.connect(lambda: self.handle("teach"))
        menu.addSeparator()
        menu.addAction("Editar correções (teach)").triggered.connect(lambda: self._open(self.cfg["corrections"]))
        menu.addAction("Editar vocabulário").triggered.connect(lambda: self._open(self.cfg["vocab"]))
        menu.addSeparator()
        menu.addAction("Sair").triggered.connect(lambda: QApplication.instance().quit())
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self._set_tray("idle")
        self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:  # clique esquerdo
            self.handle("toggle")

    def _open(self, path) -> None:
        subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _selection(self) -> str:
        """Texto selecionado (primary/clipboard) ou a última transcrição."""
        for sel in ("primary", "clipboard"):
            try:
                r = subprocess.run(["xclip", "-o", "-selection", sel],
                                   capture_output=True, text=True, timeout=1)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()[:200]
            except Exception:  # noqa: BLE001
                pass
        last = self.cfg["state_dir"] / "last.txt"
        return last.read_text(encoding="utf-8").strip()[:200] if last.is_file() else ""

    def _teach_dialog(self) -> None:
        """Abre o diálogo (não-modal) para ensinar uma correção."""
        if self._dlg is not None:
            return
        dlg = QDialog()
        dlg.setWindowTitle("Ensinar correção")
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        form = QFormLayout(dlg)
        wrong = QLineEdit(self._selection())
        right = QLineEdit()
        right.setPlaceholderText("F8 e fale (o texto entra aqui) — F9 salva e fecha")
        vocab = QCheckBox("Adicionar também ao vocabulário (viés do ASR)")
        form.addRow("Errado:", wrong)
        form.addRow("Certo:", right)
        form.addRow(vocab)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._teach_save)
        bb.rejected.connect(self._teach_close)
        form.addRow(bb)
        self._dlg, self._dlg_wrong, self._dlg_right, self._dlg_vocab = dlg, wrong, right, vocab
        dlg.show()
        dlg.activateWindow()
        dlg.raise_()
        right.setFocus()

    def _teach_save(self) -> None:
        if self._dlg is None:
            return
        w = self._dlg_wrong.text().strip()
        r = self._dlg_right.text().strip()
        v = self._dlg_vocab.isChecked()
        if w and r:
            core.cmd_teach(self.cfg, w, r, v)
        self._teach_close()

    def _teach_close(self) -> None:
        if self._dlg is not None:
            self._dlg.close()
        self._dlg = None

    def _set_tray(self, state: str) -> None:
        if state == "rec":
            self.tray.setIcon(QIcon.fromTheme("media-record"))
            self.tray.setToolTip("Dictation — GRAVANDO")
        else:
            self.tray.setIcon(QIcon.fromTheme("audio-input-microphone"))
            self.tray.setToolTip("Dictation — ocioso (F8)")

    def _watchdog(self) -> None:
        """Segurança: se o ffmpeg morrer ou o stop se perder, encerra e transcreve."""
        if not self.recording:
            return
        proc = self.proc
        expired = self.t0 and self.cfg["max_seconds"] and (time.time() - self.t0) > self.cfg["max_seconds"] + 1
        if (proc and proc.poll() is not None) or expired:
            self.recording = False
            self._gen += 1
            self._ptimer.stop()
            self.preview.set_text("")
            self.ind.hide()
            self._set_tray("idle")
            threading.Thread(target=self._finish, args=(getattr(self, "raw", None), proc), daemon=True).start()

    # ---------------------------------------------------------------- audio
    def start(self) -> str:
        if self.recording and self.proc and self.proc.poll() is None:
            return "já gravando"
        self.cfg["state_dir"].mkdir(parents=True, exist_ok=True)
        src = core.default_source(self.cfg)
        self.raw = self.cfg["state_dir"] / "rec.raw"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", src,
               "-ar", "16000", "-ac", "1", "-flush_packets", "1", "-f", "s16le"]
        if self.cfg["max_seconds"]:
            cmd += ["-t", str(self.cfg["max_seconds"])]
        cmd += [str(self.raw)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.recording = True
        self.t0 = time.time()
        self._gen += 1
        self._last_raw = 0
        self.preview.set_text("")
        if self._preview_on:
            self._ptimer.start()
        if self.cfg["indicator"]:
            self.ind.show()
            self.ind.reposition()
        else:
            core.notify(self.cfg, "🎙️ Gravando…", "")
        self._set_tray("rec")
        return "gravando"

    def stop(self) -> str:
        if not self.recording:
            return "nada gravando"
        self.recording = False
        self._gen += 1
        self._ptimer.stop()
        self.preview.set_text("")
        self.ind.hide()
        self._set_tray("idle")
        raw = getattr(self, "raw", None)
        proc = self.proc
        threading.Thread(target=self._finish, args=(raw, proc), daemon=True).start()
        return "transcrevendo"

    def _finish(self, raw, proc) -> None:
        try:
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                for _ in range(200):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                else:
                    proc.kill()
            data = raw.read_bytes() if raw and raw.is_file() else b""
            if len(data) < 8000:  # < ~0.25s de áudio
                core.notify(self.cfg, "⚠️ dictation", "gravação vazia")
                return
            wav = self.cfg["state_dir"] / "rec.wav"
            wav.write_bytes(core.raw_to_wav(data))
            text = core.transcribe(self.cfg, wav)
            text = core.apply_corrections(text, core.load_corrections(self.cfg["corrections"]))
            if text:
                core.save_last(self.cfg, text)
                core.inject(self.cfg, text)
        except Exception as exc:  # noqa: BLE001
            core.notify(self.cfg, "⚠️ dictation", str(exc)[:120])

    def _preview_tick(self) -> None:
        """Prévia ao vivo: reenvia o áudio acumulado ao Groq e atualiza a bolha."""
        if not self.recording or self._preview_busy:
            return
        raw_path = getattr(self, "raw", None)
        try:
            raw = raw_path.read_bytes() if raw_path and raw_path.is_file() else b""
        except OSError:
            return
        if len(raw) < 32000 or len(raw) == self._last_raw:  # < ~1s ou nada novo
            return
        self._last_raw = len(raw)
        gen = self._gen
        data = core.raw_to_wav(raw)
        self._preview_busy = True

        def work():
            try:
                t = core.transcribe_groq_bytes(self.cfg, data)
                t = core.apply_corrections(t, core.load_corrections(self.cfg["corrections"]))
                if gen == self._gen:
                    self.previewReady.emit(t)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._preview_busy = False

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- socket
    def handle(self, line: str) -> str:
        cmd = line.strip().lower()
        if cmd == "start":
            return self.start()
        if cmd == "stop":
            return self.stop()
        if cmd == "toggle":
            return self.stop() if self.recording else self.start()
        if cmd == "status":
            return "gravando" if self.recording else "ocioso"
        if cmd == "teach":
            if self._dlg is not None:
                self._teach_save()
                return "salvo"
            self._teach_dialog()
            return "aberto"
        if cmd == "quit":
            QTimer.singleShot(0, QApplication.instance().quit)
            return "bye"
        return "?"


def main() -> int:
    cfg = core.load_config(Path(os.path.expanduser("~/.config/dictation/config.ini")))

    # instância única (lock)
    import fcntl
    lock_path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "dictation.lock")
    lockf = open(lock_path, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.stderr.write("dictationd: já em execução\n")
        return 0

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Dictation")
    app.setApplicationDisplayName("Dictation")
    app.setDesktopFileName("dictation")
    daemon = Daemon(cfg)

    path = sock_path()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = QLocalServer()
    if not server.listen(path):
        sys.stderr.write(f"dictationd: falha no socket {path}: {server.errorString()}\n")
        return 1

    def on_conn():
        conn: QLocalSocket = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(150)
        line = bytes(conn.readAll()).decode(errors="replace")
        reply = daemon.handle(line)
        conn.write((reply + "\n").encode())
        conn.flush()
        conn.waitForBytesWritten(150)
        conn.disconnectFromServer()

    server.newConnection.connect(on_conn)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    app.aboutToQuit.connect(lambda: (server.close(), os.path.exists(path) and os.unlink(path)))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
