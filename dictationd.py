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

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

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


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.recording = False
        self.proc: subprocess.Popen | None = None
        self.ind = indmod.Indicator()
        indmod.ANCHOR = cfg["indicator_anchor"]
        self.ind.hide()

    # ---------------------------------------------------------------- audio
    def start(self) -> str:
        if self.recording and self.proc and self.proc.poll() is None:
            return "já gravando"
        self.cfg["state_dir"].mkdir(parents=True, exist_ok=True)
        src = core.default_source(self.cfg)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", src,
               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"]
        if self.cfg["max_seconds"]:
            cmd += ["-t", str(self.cfg["max_seconds"])]
        self.wav = self.cfg["state_dir"] / "rec.wav"
        cmd += [str(self.wav)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.recording = True
        if self.cfg["indicator"]:
            self.ind.show()
            self.ind.reposition()
        else:
            core.notify(self.cfg, "🎙️ Gravando…", "")
        return "gravando"

    def stop(self) -> str:
        if not self.recording:
            return "nada gravando"
        self.recording = False
        self.ind.hide()
        wav = getattr(self, "wav", None)
        proc = self.proc
        threading.Thread(target=self._finish, args=(wav, proc), daemon=True).start()
        return "transcrevendo"

    def _finish(self, wav, proc) -> None:
        try:
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                for _ in range(200):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                else:
                    proc.kill()
            if not wav or not wav.is_file() or wav.stat().st_size < 1000:
                core.notify(self.cfg, "⚠️ dictation", "gravação vazia")
                return
            text = core.transcribe(self.cfg, wav)
            text = core.apply_corrections(text, core.load_corrections(self.cfg["corrections"]))
            if text:
                core.inject(self.cfg, text)
        except Exception as exc:  # noqa: BLE001
            core.notify(self.cfg, "⚠️ dictation", str(exc)[:120])

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
