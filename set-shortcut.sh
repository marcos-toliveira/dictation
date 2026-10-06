#!/usr/bin/env bash
# ==============================================================================
# set-shortcut.sh — registra/atualiza o atalho global do dictation no KDE
# (Plasma 6 / KGlobalAccel). Plasma 6 removeu o "Comando personalizado"
# (KHotKeys não foi portado), então usamos um .desktop + entrada em
# kglobalshortcutsrc no componente "services".
#
# Uso: set-shortcut.sh [tecla]     (default: Ctrl+Alt+D)
#   ex.: set-shortcut.sh Meta+Shift+D
# ==============================================================================
set -euo pipefail
KEY="${1:-Ctrl+Alt+D}"
BIN="$HOME/.local/bin/dictation"
DESKTOP="$HOME/.local/share/applications/dictation.desktop"

[ -x "$BIN" ] || { echo "ERRO: $BIN não existe (rode install.sh primeiro)." >&2; exit 1; }

mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Ditado
Name[pt_BR]=Ditado
Comment=Ditado por voz (push-to-talk)
Comment[pt_BR]=Ditado por voz (push-to-talk)
Exec=$BIN toggle
Icon=audio-input-microphone
Terminal=false
NoDisplay=true
StartupNotify=false
Categories=Utility;
EOF

cp -p "$HOME/.config/kglobalshortcutsrc" "$HOME/.config/kglobalshortcutsrc.bak-$(date +%Y%m%d-%H%M%S)"
kwriteconfig6 --file kglobalshortcutsrc --group services --group dictation.desktop --key _launch "$KEY"
kwriteconfig6 --file kglobalshortcutsrc --group services --group dictation.desktop --key _k_friendly_name "Ditado"
kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
systemctl --user restart plasma-kglobalaccel.service

echo "✔ atalho do dictation = $KEY"
echo "  (backup do kglobalshortcutsrc salvo em ~/.config/)"
echo "  teste: aperte $KEY, fale, aperte de novo."
