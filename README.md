# dictation — push-to-talk voice typing for Linux (X11)

Dictate text into **any application** (editor, browser, chat, terminal). Press a
hotkey, speak, press again — clean, punctuated text is typed at your cursor.

Designed for **developer dictation** on modest hardware: it offloads recognition
to a fast online engine by default, keeps a warm daemon so recording starts in
~0.4s, and shows a floating **REC** badge anchored to the focused window.

## Features

- **Global hotkeys** (KDE Plasma 6 / KGlobalAccel): dictate and teach.
- **Warm daemon** (`dictationd`): indicator preloaded, commands over a local
  socket, so capture starts almost immediately (no Python/Qt boot per use).
- **Online ASR** via Groq `whisper-large-v3-turbo` (OpenAI-compatible), with a
  **vocabulary prompt** for technical terms and **deterministic corrections**.
- **Offline fallback** via local `whisper.cpp` if the network/API fails.
- **Floating indicator** (PySide6/Qt6): frameless, translucent, click-through,
  never steals focus, anchored to the active window (multi-monitor aware).
- **System tray icon**: shows the daemon is alive (mic = idle, red = recording),
  with a menu to toggle, edit corrections/vocabulary and quit.
- **Live preview**: while you speak, a floating bubble shows the running
  transcription (Groq pseudo-streaming); the accurate final text is typed on stop.
- **Teaching loop**: fix a misheard word by voice (`F9` opens a dialog → `F8`
  dictates the correct word → `F9` saves); corrections live **only on your machine**.
- **Secrets never in argv/chat**: the API key lives in a `0600` vault.

## Requirements

- Linux with **X11** (KDE Plasma 6 tested) · PipeWire or PulseAudio
- `ffmpeg`, `xdotool`, `xclip`, Python 3 + **PySide6** (Qt6)
- Optional: `whisper.cpp` (`whisper-cli` + a ggml model) for the offline fallback

Dependencies:

```bash
# Arch / Manjaro
sudo pacman -S --needed ffmpeg xdotool xclip pyside6 libnotify

# Debian / Ubuntu
sudo apt install ffmpeg xdotool xclip libnotify-bin python3-pyside6.qtwidgets

# any distro (fallback for PySide6)
pip install --user PySide6
```

## Install

```bash
git clone https://github.com/marcos-toliveira/dictation && cd dictation
bash install.sh
```

`install.sh` installs `~/.local/bin/{dictation,dictationd,dictation-indicator}`,
creates `~/.config/dictation/{config.ini,vocab.txt,corrections.tsv}` (without
overwriting existing ones), and adds the daemon autostart entry
(`~/.config/autostart/dictationd.desktop`).

### 1. API key (Groq)

Create a key at <https://console.groq.com> (free tier), then store it in a `0600`
vault — **never in the shell history or argv**:

```bash
install -d -m700 ~/.config/anubis
printf 'GROQ_API_KEY=%s\nGROQ_MODEL=whisper-large-v3-turbo\n' "$YOUR_KEY" > ~/.config/anubis/groq.env
chmod 600 ~/.config/anubis/groq.env
```

`GROQ_MODEL` is optional (default `whisper-large-v3-turbo`).

### 2. Global hotkeys (Plasma 6)

```bash
bash set-shortcut.sh              # defaults: F8 (dictate) and F9 (teach)
bash set-shortcut.sh F8 F9        # or any free keys: <dictate> <teach>
```

Plasma 6 dropped the old "Custom Shortcut" (KHotKeys) module; this registers the
shortcuts through KGlobalAccel (component `services`) and reloads it.

### 3. Use it

Press **F8** → **● REC** appears near the focused window → speak → press **F8**
again → the text is typed at your cursor.

To **teach a correction**: select the misheard word, press **F9** → dialog opens
with *Errado* prefilled → press **F8** and **speak the correct word** (it lands in
the field) → press **F9** again to **save and close**.

The daemon autostarts at login; if it is ever down, any `dictation` command
auto-starts it (or run `~/.local/bin/dictationd`).

## Configuration (`~/.config/dictation/config.ini`)

`[dictation]`

| key | meaning |
|---|---|
| `provider` | `groq` (online) or `local` (whisper.cpp) |
| `language` | `pt`, `en`, … or `auto` |
| `model` | Groq model (default `whisper-large-v3-turbo`) |
| `device` | audio source (empty = PipeWire default) |
| `max_seconds` | recording safety cap (default 180) |
| `inject` | `type` (xdotool) · `clipboard` (Ctrl+V) · `stdout` |
| `type_delay_ms` | per-character delay for `xdotool type` |
| `trailing_space` | append a space after each dictation |
| `notify` | desktop notifications (errors / when the indicator is off) |
| `indicator` | floating REC badge on/off |
| `indicator_anchor` | `bottom-right` · `bottom-center` · `top-right` · `top-left` · `mouse` |
| `preview` | live preview bubble on/off (only with `provider = groq`) |
| `preview_interval_ms` | preview refresh interval (default 1500) |
| `preview_anchor` | preview position (same options as `indicator_anchor`) |

`[paths]`: `vault`, `indicator_bin`, `vocab`, `corrections`, `state_dir`.
`[local]` (offline fallback): `whisper_bin`, `model`, `threads`.

`vocab.txt` feeds the ASR vocabulary prompt (technical terms). `corrections.tsv`
applies `wrong<TAB>right` fixes after transcription. Both are read on every use.

## CLI

```bash
dictation toggle | start | stop | status
dictation transcribe audio.wav --to stdout      # test without typing
dictation transcribe audio.wav --provider local # force offline
dictation last                                  # last transcription
dictation teach                                 # open the teach dialog
dictation teach "wrong" "right" [--vocab]       # learn a correction (no dialog)
```

Options: `--config <ini>`, `--provider groq|local`, `--to stdout|type|clipboard`,
`--vocab`.

## Teaching corrections

**By voice (fastest):**

1. **Select** the misheard word in your editor.
2. **F9** → dialog opens, *Errado* prefilled from your selection.
3. **F8** and **speak the correct word** → it is typed into *Certo* (or type it).
   Tick *vocabulary* to also bias the ASR prompt.
4. **F9** again → **saves and closes**.

**By CLI / tray menu:**

```bash
dictation teach "tu linho" "Tulinho"           # deterministic fix (corrections.tsv)
dictation teach "presel" "presell" --vocab     # also biases the ASR vocabulary prompt
dictation last                                 # last transcription (to spot errors)
```

- `corrections.tsv`: `wrong<TAB>right`, applied (case-insensitive, word-boundary)
  after every transcription.
- `vocab.txt`: terms fed to the ASR `prompt` to spell names/technical words right.

## How it works

```
hotkey → dictation (client) → socket → dictationd (warm daemon)
   start: ffmpeg/PipeWire capture (16kHz mono PCM) + show REC indicator
   while recording: every ~1.5s → Groq (running audio) → update preview bubble
   stop : stop capture → Groq (full audio, vocab prompt) → corrections → xdotool type
                         └ fallback: whisper.cpp local
```

Socket: `${XDG_RUNTIME_DIR}/dictation.sock` · single instance (lock) · tray icon.

### Live preview (pseudo-streaming)

Groq's transcription API is **batch** (no streaming), so the preview re-transcribes
the audio accumulated so far every `preview_interval_ms` and refreshes a floating
bubble. It lags ~1–2s and may revise itself; the **final** text (typed on stop) is the
one that counts. Only active when `provider = groq`. For true word-by-word streaming,
a streaming backend (e.g. Deepgram WebSocket) can be plugged in later.

## Privacy

Online mode sends your audio to the ASR provider (Groq, zero-retention by
policy). For sensitive content set `provider = local` (fully offline).

**Your corrections, vocabulary, config and API key stay on your machine**
(`~/.config/dictation/` and the `0600` vault) — they are **not** in this repository
(the repo ships only `*.example` templates and `.gitignore` excludes the real files).

## Uninstall

```bash
rm -f ~/.local/bin/{dictation,dictationd,dictation-indicator}
rm -f ~/.config/autostart/dictationd.desktop
rm -f ~/.local/share/applications/dictation.desktop ~/.local/share/applications/dictation-teach.desktop
rm -rf ~/.config/dictation ~/.local/state/dictation
# then remove the [services][dictation*.desktop] groups from ~/.config/kglobalshortcutsrc
```

## License

MIT — see [LICENSE](LICENSE).
