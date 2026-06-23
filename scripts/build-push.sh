#!/usr/bin/env bash
#
# build-push.sh — build the per-cloud Terrakettle images, scan them with
# Docker Scout, and push only if the scan gate passes.
#
# This is the manual release pipeline (there is no CI). It exists so a
# regressed image can't be published silently, periodic rebuilds (which
# absorb upstream base/tool CVE patches) are one command, and every tag is
# pushed fully-qualified as "$REPO:<tag>" so a typo can never spawn a stray
# repository on Docker Hub (Hub auto-creates a repo on first push of a name).
#
# Usage:
#   scripts/build-push.sh <version> [cloud ...]
#
#   scripts/build-push.sh 0.1.0                  # all flavours: local aws azure gcp
#   scripts/build-push.sh 0.1.0 gcp              # just one
#   DRY_RUN=1 scripts/build-push.sh 0.1.0        # build + scan, do NOT push
#   GATE=high scripts/build-push.sh 0.1.0        # stricter gate (default: critical)
#   GATE=none scripts/build-push.sh 0.1.0        # report only, never block
#   ALLOW_KNOWN=1 scripts/build-push.sh 0.1.0    # push despite gate (acknowledged CVEs)
#
# The "local" flavour is the default backend (versitygw/local storage); it is
# additionally published as :latest and the bare :<version> tag.
#
# Env:
#   REPO        Docker Hub repo            (default: wecloudes/terrakettle)
#   PLATFORMS   buildx target platforms    (default: linux/amd64,linux/arm64)
#   GATE        critical | high | none     (default: critical)
#   DRY_RUN     1 = build + scan only
#   ALLOW_KNOWN 1 = push even if the gate trips (e.g. unfixable upstream CVEs)
#
set -euo pipefail

VERSION="${1:?usage: build-push.sh <version> [cloud ...]}"
shift || true
CLOUDS=("$@")
[ "${#CLOUDS[@]}" -eq 0 ] && CLOUDS=(local aws azure gcp)

REPO="${REPO:-wecloudes/terrakettle}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
GATE="${GATE:-critical}"
DRY_RUN="${DRY_RUN:-0}"
ALLOW_KNOWN="${ALLOW_KNOWN:-0}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

have_scout=1
docker scout version >/dev/null 2>&1 || have_scout=0
[ "$have_scout" -eq 0 ] && echo "⚠️  docker scout not installed — scan gate disabled"

# Scan one locally-loaded image. Returns non-zero if the gate level is tripped.
scan_gate() {
  local img="$1"
  [ "$have_scout" -eq 0 ] && return 0
  echo "🔎 Scanning $img (gate: $GATE) ..."
  docker scout cves "$img" --only-severity critical,high \
    --format packages 2>/dev/null | tail -n 40 || true
  case "$GATE" in
    none) return 0 ;;
    critical) docker scout cves "$img" --only-severity critical --exit-code >/dev/null 2>&1 ;;
    high)     docker scout cves "$img" --only-severity critical,high --exit-code >/dev/null 2>&1 ;;
    *) echo "unknown GATE=$GATE" >&2; return 1 ;;
  esac
}

# Echo the fully-qualified push tags for a given flavour. The "local" flavour
# is the default backend, so it also takes :latest and the bare :<version>.
#
# Tags use the ${REPO}:tag brace form on purpose: the brace closes the
# parameter so a ":l" can never be read as a shell modifier (zsh's ${VAR:l}
# lowercases — that footgun is what spawned the stray terrakettle{atest,ocal}
# repos). Harmless in bash, defensive everywhere.
tags_for() {
  local c="$1"
  if [ "$c" = "local" ]; then
    echo "${REPO}:latest ${REPO}:local ${REPO}:${VERSION}"
  else
    echo "${REPO}:${c} ${REPO}:${c}-${VERSION}"
  fi
}

for c in "${CLOUDS[@]}"; do
  local_tag="${REPO}:${c}-scan"
  echo "=============================================================="
  echo "=== $c — build (native arch) for scan ==="
  echo "=============================================================="
  docker buildx build --build-arg CLOUD="$c" -t "$local_tag" --load .

  if scan_gate "$local_tag"; then
    echo "✅ $c: scan gate passed ($GATE)"
  else
    echo "❌ $c: scan gate tripped at level '$GATE'"
    if [ "$ALLOW_KNOWN" = "1" ]; then
      echo "⚠️  ALLOW_KNOWN=1 — proceeding despite findings"
    else
      echo "   Re-run with ALLOW_KNOWN=1 to push anyway (acknowledged/unfixable),"
      echo "   GATE=none to report-only, or GATE=high to relax."
      exit 1
    fi
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "🚫 DRY_RUN=1 — skipping push for $c"
    continue
  fi

  # Assemble -t args from the canonical tag list (never concatenate by hand).
  tag_args=()
  for t in $(tags_for "$c"); do tag_args+=(-t "$t"); done

  echo "=== $c — multi-arch build + push ($PLATFORMS) ==="
  docker buildx build --platform "$PLATFORMS" --build-arg CLOUD="$c" \
    "${tag_args[@]}" --push .
  echo "📤 pushed: $(tags_for "$c")"
done

echo "ALL DONE"
