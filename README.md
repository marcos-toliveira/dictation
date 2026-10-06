# dictation — push-to-talk voice typing for Linux (X11)

Dictate text into **any application** (editor, browser, chat, terminal). Press a
hotkey, speak, press again — clean, punctuated text is typed at your cursor.

Designed for **developer dictation** on modest hardware: it offloads recognition
to a fast online engine by default, keeps a warm daemon so recording starts in
~0.4s, and shows a floating **REC** badge anchored to the focused window.

## Features

- **Global hotkey** (KDE Plasma 6 / KGlobalAccel) — no desktop-specific hacks.
- **Warm daemon** (`dictationd`): the indicator is preloaded and commands go over a
  local socket, so capture starts almost immediately (no Python/Qt boot per use).
- **Online ASR** via Groq `whisper-large-v3-turbo` (OpenAI-compatible), with a
  **vocabulary prompt** for technical terms and **deterministic corrections**.
- **Offline fallback** via local `whisper.cpp` if the network/API fails.
- **Floating indicator** (PySide6/Qt6): frameless, translucent, click-through,
  never steals focus, anchored to the active window (multi-monitor aware).
- **System tray icon**: shows the daemon is alive (mic = idle, red = recording),
  with a menu to toggle, edit corrections/vocabulary and quit.
- **Teaching loop**: `dictation teach "wrong" "right"` records a correction (and
  optionally adds the term to the vocabulary prompt); `dictation last` shows the
  last transcription so you can spot what to fix.
- **Secrets never in argv/chat**: the API key lives in a `0600` vault.

## Requirements

- Linux with **X11** (KDE Plasma 6 tested) · PipeWire or PulseAudio
- `ffmpeg`, `xdotool`, `xclip`, Python 3 + `PySide6` (Qt6)
- Optional: `whisper.cpp` (`whisper-cli` + a ggml model) for the offline fallback

## Install

```bash
git clone <this-repo> dictation && cd dictation
bash install.sh
```

`install.sh` installs `~/.local/bin/{dictation,dictationd,dictation-indicator}`,
creates `~/.config/dictation/{config.ini,vocab.txt,corrections.tsv}`, and adds an
autostart entry for the daemon.

### 1. API key (Groq)

Create a key at <https://console.groq.com> (free tier), then store it in a `0600`
vault — **never in the shell history or argv**:

```bash
install -d -m700 ~/.config/anubis
printf 'GROQ_API_KEY=%s\nGROQ_MODEL=whisper-large-v3-turbo\n' "$YOUR_KEY" > ~/.config/anubis/groq.env
chmod 600 ~/.config/anubis/groq.env
```

### 2. Global hotkey (Plasma 6)

```bash
bash set-shortcut.sh            # default: Ctrl+Alt+D
bash set-shortcut.sh F8         # or any free key
```

Plasma 6 dropped the old "Custom Shortcut" (KHotKeys) module; this registers the
shortcut through KGlobalAccel (`services` component).

### 3. Use it

Press the hotkey → **● REC** appears near the focused window → speak → press
again → the text is typed at your cursor.

## Configuration (`~/.config/dictation/config.ini`)

| key | meaning |
|---|---|
| `provider` | `groq` (online) or `local` (whisper.cpp) |
| `language` | `pt`, `en`, … or `auto` |
| `model` | Groq model (default `whisper-large-v3-turbo`) |
| `inject` | `type` (xdotool) · `clipboard` (Ctrl+V) · `stdout` |
| `device` | audio source (empty = PipeWire default) |
| `indicator_anchor` | `bottom-right` · `bottom-center` · `top-right` · `top-left` · `mouse` |

`vocab.txt` feeds the ASR vocabulary prompt (technical terms). `corrections.tsv`
applies `wrong<TAB>right` fixes after transcription.

## CLI

```bash
dictation toggle | start | stop | status
dictation transcribe audio.wav --to stdout   # test without typing
dictation transcribe audio.wav --provider local
dictation last                               # last transcription
dictation teach "wrong" "right" [--vocab]    # learn a correction
```

## Teaching corrections

When the transcriber gets something wrong, teach it — no restart needed:

```bash
dictation teach "tu linho" "Tulinho"          # deterministic fix (corrections.tsv)
dictation teach "presel" "presell" --vocab    # also biases the ASR vocabulary prompt
```

- `corrections.tsv`: `wrong<TAB>right`, applied (case-insensitive, word-boundary)
  after every transcription.
- `vocab.txt`: terms fed to the ASR `prompt` to spell names/technical words right.
- Both files are read on each use, so changes apply immediately. You can also edit
  them from the tray menu.

## How it works

```
hotkey → dictation (client) → socket → dictationd (warm daemon)
   start: ffmpeg/PipeWire capture (16kHz mono) + show REC indicator
   stop : stop capture → Groq (vocab prompt) → corrections → xdotool type
                         └ fallback: whisper.cpp local
```

## Privacy

Online mode sends your audio to the ASR provider (Groq, zero-retention by
policy). For sensitive content set `provider = local` (fully offline).

## License

MIT — see [LICENSE](LICENSE).
