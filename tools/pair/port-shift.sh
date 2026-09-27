#!/usr/bin/env bash
# Build a PAIR whose listen ports are shifted by +10000 (14318–14323 → 24318–24323, Ollama proxy 11434 → 21434,
# LM-slot proxy 1234 → 11234). Only needed on a machine that also terminates an SSH tunnel to a remote PAIR node:
# PAIR's ports are fixed and peers dial each other on them, so the tunnel has to own the stock ports on 127.0.0.1
# and the local PAIR has to move (docs/pipeline-design.md §9). On a real LAN none of this is needed.
#
# The shift is a patch applied to a COPY of the PAIR source; the PAIR checkout itself is not modified.
#   tools/pair/port-shift.sh /path/to/Personal-AI-Router [GOOS GOARCH]
set -euo pipefail
SRC=${1:?path to the PAIR checkout}; GOOS_=${2:-$(go env GOOS)}; GOARCH_=${3:-$(go env GOARCH)}
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$SRC/services-shift"
rm -rf "$OUT.tmp" && mkdir -p "$OUT.tmp"
rsync -a --exclude build --exclude build-linux "$SRC/services/" "$OUT.tmp/"
(cd "$OUT.tmp" && patch -p1 -s < "$HERE/port-shift.patch")
rm -rf "$OUT" && mv "$OUT.tmp" "$OUT"
(cd "$OUT" && GOOS=$GOOS_ GOARCH=$GOARCH_ CGO_ENABLED=0 ./build.sh)
echo "shifted binaries: $OUT/build/bin  → copy into <PAIR>/desktop/cli-bin/ (keep a backup of the stock ones)"
