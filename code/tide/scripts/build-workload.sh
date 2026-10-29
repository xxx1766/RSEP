#!/usr/bin/env bash
# Build the Tide ablation workload image and import it into containerd's
# "default" namespace so cmd/resptest / cmd/httpbase can pull it by ref.
#
# Usage: sudo scripts/build-workload.sh [IMAGE_TAG]
#
# Requires: docker (for build) + ctr (for containerd import).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:-tide/workload:yolox-nano}"
CTX="$REPO_ROOT/workload"
# Tide's containerxv2 looks up images in the "tide" namespace and appends
# ":<GOARCH>" (cmd/resptestv2/exp.go:36-38). Mirror as :amd64 in tide ns so
# the EI=on harness finds the image without code changes.
TIDE_TAG="${TAG%%:*}:amd64"   # tide/workload:yolox-nano → tide/workload:amd64

if ! command -v docker >/dev/null; then
  echo "docker not found on PATH" >&2; exit 1
fi
if ! command -v ctr >/dev/null; then
  echo "ctr not found on PATH" >&2; exit 1
fi

echo "[build-workload] building $TAG from $CTX"
docker build -t "$TAG" "$CTX"

echo "[build-workload] exporting + importing into containerd (namespace: default)"
docker save "$TAG" | ctr -n default images import -

echo "[build-workload] verifying image in containerd default ns:"
ctr -n default images ls | grep -qE "^docker.io/$TAG" || {
  echo "import verification failed (default ns)"; exit 1;
}

echo "[build-workload] re-importing into containerd tide ns as $TIDE_TAG"
docker save "$TAG" | ctr -n tide images import - >/dev/null
ctr -n tide images tag --force "docker.io/$TAG" "docker.io/$TIDE_TAG" >/dev/null
ctr -n tide images ls | grep -qE "^docker.io/$TIDE_TAG" || {
  echo "import verification failed (tide ns)"; exit 1;
}

echo "[build-workload] OK: $TAG (default) + $TIDE_TAG (tide) available"
