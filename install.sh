#!/bin/sh
# Put aidecamp on PATH. Nothing else is installed: hooks are passed per session via --settings.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
chmod +x "$here/bin/aidecamp" "$here/bin/cmux"
mkdir -p "$HOME/.local/bin"
ln -sf "$here/bin/aidecamp" "$HOME/.local/bin/aidecamp"
ln -sf "$here/bin/cmux" "$HOME/.local/bin/cmux"
echo "linked $HOME/.local/bin/aidecamp -> $here/bin/aidecamp"
echo "linked $HOME/.local/bin/cmux -> $here/bin/cmux (compatibility alias)"
