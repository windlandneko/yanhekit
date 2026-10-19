#!/bin/sh
# Install uv for yanhekit when it is missing, and print the path to write in place of `uv` until the harness restarts.
if command -v uv >/dev/null 2>&1; then
  echo "uv is ready: $(command -v uv)"
  exit 0
fi
case $(uname -s) in
  MINGW* | MSYS* | CYGWIN*) exec powershell -NoProfile -ExecutionPolicy Bypass -File "$(dirname "$0")/setup.ps1" ;;
esac

dir=${UV_INSTALL_DIR:-$HOME/.local/bin}
if ! [ -x "$dir/uv" ]; then
  echo 'Downloading uv, about 15 MB; on a slow connection this takes a few minutes.' >&2
  installer=$(mktemp)
  trap 'rm -f "$installer"' EXIT
  for url in https://astral.sh/uv/install.sh https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh; do
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf --max-time 60 -o "$installer" "$url"
    else
      wget -q -T 60 -O "$installer" "$url"
    fi && UV_INSTALL_DIR=$dir sh "$installer" >&2 && break
    echo "Could not install from $url" >&2
  done
fi
if ! [ -x "$dir/uv" ]; then
  echo 'Could not download uv. In a sandbox, run this script again with network access; otherwise see https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
echo "uv installed: $dir/uv"
echo "New sessions find it as uv; until the harness restarts, write $dir/uv in place of uv."
