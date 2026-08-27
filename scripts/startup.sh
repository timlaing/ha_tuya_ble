#!/usr/bin/zsh
set -e

cd "$(realpath "$(dirname "$0")/..")"

export UV_LINK_MODE=copy

echo "Installing development dependencies..."
uv pip install \
  -r requirements.txt \
  -r requirements-dev.txt \
  --upgrade \
  --config-settings editable_mode=compat
