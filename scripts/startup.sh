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

if command -v npm >/dev/null 2>&1; then
  if [ -f package-lock.json ]; then
    npm ci
  else
    npm install
  fi
fi
