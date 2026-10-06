#!/usr/bin/env bash
# ==============================================================================
# set-shortcut.sh — registra/atualiza os atalhos globais do dictation no KDE
# (Plasma 6 / KGlobalAccel). Plasma 6 removeu o "Comando personalizado"
# (KHotKeys não foi portado), então usamos .desktop + kglobalshortcutsrc
# no componente "services".
#
# Uso: set-shortcut.sh [tecla_ditado] [tecla_ensinar]
#   default: F8 (ditar) e F9 (ensinar correção)
# ==============================================================================
set -euo pipefail
KEY="${1:-F8}"
TEACH_KEY="${2:-F9}"
BIN="$HOME/.local/bin/dictation"
APP_DIR="$HOME/.local/share/applications"

[ -x "$BIN" ] || { echo "ERRO: $BIN não existe (rode install.sh primeiro)." >&2; exit 1; }

mkdir -p "$APP_DIR"
write_desktop() { # $1=arquivo $2=nome $3=exec
    cat > "$APP_DIR/$1" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Name[pt_BR]=$2
Comment=Ditado por voz (push-to-talk)
Comment[pt_BR]=Ditado por voz (push-to-talk)
Exec=$3
Icon=audio-input-microphone
Terminal=false
NoDisplay=true
StartupNotify=false
Categories=Utility;
EOF
}
write_desktop "dictation.desktop" "Ditado" "$BIN toggle"
write_desktop "dictation-teach.desktop" "Ditado: ensinar correção" "$BIN teach"

cp -p "$HOME/.config/kglobalshortcutsrc" "$HOME/.config/kglobalshortcutsrc.bak-$(date +%Y%m%d-%H%M%S)"
kwriteconfig6 --file kglobalshortcutsrc --group services --group dictation.desktop       --key _launch "$KEY"
kwriteconfig6 --file kglobalshortcutsrc --group services --group dictation.desktop       --key _k_friendly_name "Ditado"
kwriteconfig6 --file kglobalshortcutsrc --group services --group dictation-teach.desktop --key _launch "$TEACH_KEY"
kwriteconfig6 --file kglobalshortcutsrc --group services --group dictation-teach.desktop --key _k_friendly_name "Ensinar correção"
kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
systemctl --user restart plasma-kglobalaccel.service

echo "✔ ditado   = $KEY"
echo "✔ ensinar  = $TEACH_KEY  (selecione o texto errado e aperte)"
echo "  (backup do kglobalshortcutsrc salvo em ~/.config/)"
