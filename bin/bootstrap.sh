#!/usr/bin/env bash
# One-line installer for either side. Clones the repo to ~/local_infer and runs the matching installer.
#
#   model side (blank GPU box / Mac node):
#     curl -fsSL https://raw.githubusercontent.com/murphyren225/local_infer/main/bin/bootstrap.sh | bash
#   personal side (your own machine):
#     curl -fsSL https://raw.githubusercontent.com/murphyren225/local_infer/main/bin/bootstrap.sh | bash -s -- personal http://<hub>:4000
#
# Extra arguments after the side name are passed to the installer (e.g. `model --join URL --token T`).
set -euo pipefail
SIDE="${1:-model}"; shift || true
DIR="${LOCAL_INFER_DIR:-$HOME/local_infer}"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only || true
else git clone -q https://github.com/murphyren225/local_infer.git "$DIR"; fi
case "$SIDE" in
  model)    exec "$DIR/bin/install.sh" "$@" ;;
  personal) exec "$DIR/bin/install-client.sh" "$@" ;;
  *) echo "usage: bootstrap.sh [model [installer flags] | personal http://<hub>:4000]"; exit 1 ;;
esac
