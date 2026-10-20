#!/bin/sh
# Put claudesec on PATH. Nothing else is installed: hooks are passed per session via --settings.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
chmod +x "$here/bin/claudesec"
mkdir -p "$HOME/.local/bin"
ln -sf "$here/bin/claudesec" "$HOME/.local/bin/claudesec"
rm -f "$HOME/.local/bin/cmux" "$HOME/.local/bin/aidecamp"
echo "linked $HOME/.local/bin/claudesec -> $here/bin/claudesec"
