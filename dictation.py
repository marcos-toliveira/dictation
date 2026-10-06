#!/usr/bin/env python3
# ==============================================================================
# dictation — ditado por voz "push-to-talk" para qualquer app (X11)
#
# Fluxo: atalho do KDE -> `dictation toggle` -> grava com ffmpeg/PipeWire ->
#        envia ao provedor (Groq online por padrão; fallback whisper.cpp local)
#        -> aplica correções determinísticas -> digita no app em foco (xdotool).
#
# Segredos NUNCA passam por argv: a chave é lida do cofre 0600
# (~/.config/anubis/groq.env) em memória.
#
# Uso:
#   dictation start|stop|toggle|status
#   dictation transcribe <arquivo.wav> [--to stdout|type|clipboard]
# Opções: --config <ini>  --provider groq|local  --to stdout|type|clipboard
# ==============================================================================
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HOME = Path.home()
DEFAULT_CONFIG = HOME / ".config" / "dictation" / "config.ini"


# ----------------------------------------------------------------- config
def load_config(path: Path) -> dict:
    import configparser

    cp = configparser.ConfigParser()
    if path.is_file():
        cp.read(str(path))
    cfg = {
        "provider": cp.get("dictation", "provider", fallback="groq"),
        "language": cp.get("dictation", "language", fallback="pt"),
        "model": cp.get("dictation", "model", fallback="whisper-large-v3-turbo"),
        "device": cp.get("dictation", "device", fallback=""),
        "max_seconds": cp.getint("dictation", "max_seconds", fallback=180),
        "inject": cp.get("dictation", "inject", fallback="type"),
        "type_delay_ms": cp.getint("dictation", "type_delay_ms", fallback=6),
        "trailing_space": cp.getboolean("dictation", "trailing_space", fallback=True),
        "notify": cp.getboolean("dictation", "notify", fallback=True),
        "indicator": cp.getboolean("dictation", "indicator", fallback=True),
        "indicator_anchor": cp.get("dictation", "indicator_anchor", fallback="bottom-right"),
        "indicator_bin": Path(os.path.expanduser(cp.get("paths", "indicator_bin", fallback="~/.local/bin/dictation-indicator"))),
        "vault": Path(os.path.expanduser(cp.get("paths", "vault", fallback="~/.config/anubis/groq.env"))),
        "vocab": Path(os.path.expanduser(cp.get("paths", "vocab", fallback="~/.config/dictation/vocab.txt"))),
        "corrections": Path(os.path.expanduser(cp.get("paths", "corrections", fallback="~/.config/dictation/corrections.tsv"))),
        "state_dir": Path(os.path.expanduser(cp.get("paths", "state_dir", fallback="~/.local/state/dictation"))),
        "whisper_bin": Path(os.path.expanduser(cp.get("local", "whisper_bin", fallback="~/.local/bin/whisper-cli"))),
        "whisper_model": Path(os.path.expanduser(cp.get("local", "model", fallback="~/.local/share/whisper.cpp/models/ggml-small.bin"))),
        "whisper_threads": cp.getint("local", "threads", fallback=4),
    }
    return cfg


def notify(cfg: dict, title: str, body: str) -> None:
    if cfg["notify"] and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "dictation", title, body], check=False)


def read_vault_env(path: Path) -> dict:
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ----------------------------------------------------------------- text helpers
def load_vocab(path: Path, limit: int = 800) -> str:
    if not path.is_file():
        return ""
    terms = [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    prompt = ", ".join(terms)
    return prompt[:limit]


def load_corrections(path: Path) -> list[tuple[re.Pattern, str]]:
    rules: list[tuple[re.Pattern, str]] = []
    if not path.is_file():
        return rules
    for ln in path.read_text().splitlines():
        ln = ln.rstrip("\n")
        if not ln or ln.lstrip().startswith("#"):
            continue
        if "\t" in ln:
            src, dst = ln.split("\t", 1)
        elif "=>" in ln:
            src, dst = ln.split("=>", 1)
        else:
            continue
        src, dst = src.strip(), dst.strip()
        if not src:
            continue
        pat = re.compile(rf"\b{re.escape(src)}\b", re.IGNORECASE) if src.isalnum() else re.compile(re.escape(src), re.IGNORECASE)
        rules.append((pat, dst))
    return rules


def apply_corrections(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    for pat, dst in rules:
        text = pat.sub(dst, text)
    return text


# ----------------------------------------------------------------- ASR: groq
def transcribe_groq(cfg: dict, wav: Path) -> str:
    env = read_vault_env(cfg["vault"])
    key = env.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError(f"GROQ_API_KEY ausente em {cfg['vault']}")
    model = env.get("GROQ_MODEL") or cfg["model"]
    data = wav.read_bytes()
    bnd = uuid.uuid4().hex
    fields = {"model": model, "response_format": "json"}
    if cfg["language"] and cfg["language"] != "auto":
        fields["language"] = cfg["language"]
    vocab = load_vocab(cfg["vocab"])
    if vocab:
        fields["prompt"] = vocab
    parts = [f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{bnd}--\r\n".encode())
    conn = http.client.HTTPSConnection("api.groq.com", timeout=90)
    try:
        conn.request("POST", "/openai/v1/audio/transcriptions", b"".join(parts),
                     {"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={bnd}"})
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    if resp.status != 200:
        raise RuntimeError(f"Groq HTTP {resp.status}: {body[:200].decode(errors='replace')}")
    return json.loads(body).get("text", "").strip()


# ----------------------------------------------------------------- ASR: local
def transcribe_local(cfg: dict, wav: Path) -> str:
    if not cfg["whisper_bin"].is_file():
        raise RuntimeError(f"whisper-cli não encontrado em {cfg['whisper_bin']}")
    cmd = [str(cfg["whisper_bin"]), "-m", str(cfg["whisper_model"]), "-f", str(wav),
           "-l", cfg["language"] or "auto", "-t", str(cfg["whisper_threads"]), "-nt", "-np", "-bs", "1"]
    vocab = load_vocab(cfg["vocab"])
    if vocab:
        cmd += ["--prompt", vocab]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"whisper-cli falhou: {out.stderr[:200]}")
    return " ".join(l.strip() for l in out.stdout.splitlines() if l.strip()).strip()


def transcribe(cfg: dict, wav: Path, provider: str | None = None) -> str:
    prov = (provider or cfg["provider"]).lower()
    if prov == "local":
        return transcribe_local(cfg, wav)
    try:
        return transcribe_groq(cfg, wav)
    except Exception as exc:  # noqa: BLE001 — fallback offline
        sys.stderr.write(f"[dictation] groq falhou ({exc}); caindo para whisper local\n")
        return transcribe_local(cfg, wav)


# ----------------------------------------------------------------- recording
def state_file(cfg: dict) -> Path:
    return cfg["state_dir"] / "rec.json"


def rec_alive(pid: int) -> bool:
    return pid > 0 and Path(f"/proc/{pid}").exists()


def default_source(cfg: dict) -> str:
    if cfg["device"]:
        return cfg["device"]
    if shutil.which("pactl"):
        r = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return "default"


def do_start(cfg: dict) -> int:
    sf = state_file(cfg)
    st = json.loads(sf.read_text()) if sf.is_file() else {}
    if st and rec_alive(int(st.get("pid", 0))):
        print("já gravando (pid %s)" % st["pid"])
        return 0
    cfg["state_dir"].mkdir(parents=True, exist_ok=True)
    wav = cfg["state_dir"] / "rec.wav"
    src = default_source(cfg)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", src,
           "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"]
    if cfg["max_seconds"]:
        cmd += ["-t", str(cfg["max_seconds"])]
    cmd += [str(wav)]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.4)
    if proc.poll() is not None:
        print("ERRO: ffmpeg não iniciou a gravação (fonte: %s)" % src, file=sys.stderr)
        return 1
    ind_pid = 0
    if cfg["indicator"] and cfg["indicator_bin"].is_file():
        ind = subprocess.Popen([str(cfg["indicator_bin"]), cfg["indicator_anchor"]],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
        ind_pid = ind.pid
    else:
        notify(cfg, "🎙️ Gravando…", "fale e chame o atalho de novo para parar")
    sf.write_text(json.dumps({"pid": proc.pid, "ind_pid": ind_pid, "wav": str(wav), "t0": time.time()}))
    print("gravando (pid %d, fonte %s)" % (proc.pid, src))
    return 0


def do_stop(cfg: dict, to: str | None, provider: str | None) -> int:
    sf = state_file(cfg)
    if not sf.is_file():
        print("não há gravação em andamento")
        return 0
    st = json.loads(sf.read_text())
    pid = int(st.get("pid", 0))
    ind_pid = int(st.get("ind_pid", 0))
    if ind_pid:
        try:
            os.kill(ind_pid, signal.SIGTERM)
        except OSError:
            pass
    wav = Path(st.get("wav", ""))
    if rec_alive(pid):
        os.kill(pid, signal.SIGINT)
        for _ in range(100):
            if not rec_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
    try:
        sf.unlink()
    except OSError:
        pass
    if not wav.is_file() or wav.stat().st_size < 1000:
        notify(cfg, "⚠️ dictation", "gravação vazia")
        print("gravação vazia", file=sys.stderr)
        return 1
    notify(cfg, "⏳ Transcrevendo…", "")
    text = transcribe(cfg, wav, provider)
    text = apply_corrections(text, load_corrections(cfg["corrections"]))
    if not text:
        notify(cfg, "⚠️ dictation", "nada reconhecido")
        print("", end="")
        return 0
    inject(cfg, text, to)
    return 0


# ----------------------------------------------------------------- injection
def inject(cfg: dict, text: str, to: str | None = None) -> None:
    mode = (to or cfg["inject"]).lower()
    if mode == "stdout":
        print(text)
        return
    payload = text + (" " if cfg["trailing_space"] else "")
    if mode == "clipboard":
        subprocess.run(["xclip", "-selection", "clipboard"], input=payload, text=True, check=True)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)
        return
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", str(cfg["type_delay_ms"]), "--", payload], check=False)


# ----------------------------------------------------------------- CLI
def _daemon_socket() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "dictation.sock")


def ensure_daemon() -> bool:
    """Garante o daemon no ar (auto-inicia destacado se preciso)."""
    if os.path.exists(_daemon_socket()):
        return True
    binp = os.path.expanduser("~/.local/bin/dictationd")
    if not os.path.exists(binp):
        return False
    try:
        subprocess.Popen([binp], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    for _ in range(80):
        if os.path.exists(_daemon_socket()):
            return True
        time.sleep(0.05)
    return os.path.exists(_daemon_socket())


def send_daemon(cmd: str) -> str | None:
    """Envia um comando ao dictationd quente; None se o daemon não estiver no ar."""
    path = _daemon_socket()
    if not os.path.exists(path):
        return None
    import socket as _socket
    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(path)
            s.sendall((cmd + "\n").encode())
            return s.recv(256).decode(errors="replace").strip()
    except OSError:
        return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="dictation", description="ditado por voz push-to-talk")
    ap.add_argument("cmd", choices=["start", "stop", "toggle", "status", "transcribe"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--provider", choices=["groq", "local"])
    ap.add_argument("--to", choices=["stdout", "type", "clipboard"])
    args = ap.parse_args(argv)

    cfg = load_config(Path(os.path.expanduser(args.config)))

    # daemon quente (se no ar e sem overrides) — captura em ~50ms
    if args.cmd in ("start", "stop", "toggle", "status") and not args.to and not args.provider:
        if args.cmd in ("start", "toggle", "status"):
            ensure_daemon()
        reply = send_daemon(args.cmd)
        if reply is not None:
            print(reply)
            return 0

    if args.cmd == "start":
        return do_start(cfg)
    if args.cmd == "status":
        sf = state_file(cfg)
        if sf.is_file() and rec_alive(int(json.loads(sf.read_text()).get("pid", 0))):
            print("gravando")
        else:
            print("ocioso")
        return 0
    if args.cmd == "stop":
        return do_stop(cfg, args.to, args.provider)
    if args.cmd == "transcribe":
        if not args.file:
            ap.error("transcribe exige <arquivo.wav>")
        wav = Path(args.file)
        text = transcribe(cfg, wav, args.provider)
        text = apply_corrections(text, load_corrections(cfg["corrections"]))
        inject(cfg, text, args.to or "stdout")
        return 0
    # toggle
    sf = state_file(cfg)
    if sf.is_file() and rec_alive(int(json.loads(sf.read_text()).get("pid", 0))):
        return do_stop(cfg, args.to, args.provider)
    return do_start(cfg)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
