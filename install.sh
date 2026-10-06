#!/usr/bin/env bash
# ==============================================================================
# install.sh — instala o dictation (push-to-talk) para o usuário atual
#   • binário -> ~/.local/bin/dictation
#   • config  -> ~/.config/dictation/{config.ini,vocab.txt,corrections.tsv}
# Não sobrescreve configs existentes.
# ==============================================================================
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CFG_DIR="$HOME/.config/dictation"
STATE_DIR="$HOME/.local/state/dictation"

mkdir -p "$BIN_DIR" "$CFG_DIR" "$STATE_DIR"
install -m 755 "$SRC/dictation.py" "$BIN_DIR/dictation.py"
install -m 755 "$SRC/dictation" "$BIN_DIR/dictation"
install -m 755 "$SRC/dictation-indicator.py" "$BIN_DIR/dictation-indicator"
install -m 755 "$SRC/dictationd.py" "$BIN_DIR/dictationd"

# Autostart do daemon quente (baixa latência) na sessão gráfica
AUTO_DIR="$HOME/.config/autostart"
mkdir -p "$AUTO_DIR"
cat > "$AUTO_DIR/dictationd.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Dictation daemon
Name[pt_BR]=Daemon de ditado
Comment=Daemon quente do ditado por voz (push-to-talk)
Exec=$BIN_DIR/dictationd
Icon=audio-input-microphone
Terminal=false
NoDisplay=true
X-KDE-autostart-after=panel
X-GNOME-Autostart-enabled=true
EOF
echo "criado:  $AUTO_DIR/dictationd.desktop"

for pair in "config.ini.example:config.ini" "vocab.txt.example:vocab.txt" "corrections.tsv.example:corrections.tsv"; do
    src="$SRC/${pair%%:*}"; dst="$CFG_DIR/${pair##*:}"
    if [ -e "$dst" ]; then
        echo "mantido: $dst"
    else
        install -m 644 "$src" "$dst"; echo "criado:  $dst"
    fi
done

echo
echo "✔ instalado: $BIN_DIR/dictation"
echo
echo "Registre o atalho global (Plasma 6 / KGlobalAccel):"
echo "  bash \"$SRC/set-shortcut.sh\"            # default Ctrl+Alt+D"
echo
echo "Teste sem atalho:  dictation start ; fale ; dictation stop"
echo "Teste offline:     dictation transcribe arquivo.wav --to stdout"
