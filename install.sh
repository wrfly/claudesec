#!/bin/sh
# Put aidecamp on PATH. Nothing else is installed: hooks are passed per session via --settings.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
chmod +x "$here/bin/aidecamp"
mkdir -p "$HOME/.local/bin"
ln -sf "$here/bin/aidecamp" "$HOME/.local/bin/aidecamp"
rm -f "$HOME/.local/bin/cmux"
echo "linked $HOME/.local/bin/aidecamp -> $here/bin/aidecamp"
