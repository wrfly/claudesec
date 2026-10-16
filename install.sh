#!/bin/sh
# Put cmux on PATH. Nothing else is installed: hooks are passed per session via --settings.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
chmod +x "$here/bin/cmux"
mkdir -p "$HOME/.local/bin"
ln -sf "$here/bin/cmux" "$HOME/.local/bin/cmux"
echo "linked $HOME/.local/bin/cmux -> $here/bin/cmux"
