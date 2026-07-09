#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../release_tag.sh"
fail() { echo "FAIL: $1"; exit 1; }

# compute_tag
[ "$(printf '' | compute_tag 2026.07.09)" = "v2026.07.09" ] || fail "fresh day"
[ "$(printf 'v2026.07.09\n' | compute_tag 2026.07.09)" = "v2026.07.09-2" ] || fail "collision -> -2"
[ "$(printf 'v2026.07.09\nv2026.07.09-2\n' | compute_tag 2026.07.09)" = "v2026.07.09-3" ] || fail "-2 taken -> -3"
[ "$(printf 'phase-p1\nv2026.07.08\n' | compute_tag 2026.07.09)" = "v2026.07.09" ] || fail "unrelated tags ignored"

# select_prev
[ "$(printf 'phase-p1\nv2026.07.09\nv2026.07.10\nv2026.07.09foo\n' | select_prev)" = "v2026.07.10" ] || fail "newest calver, ignore junk+phase"
[ "$(printf 'v2026.07.09\nv2026.07.09-2\nv2026.07.09-10\n' | select_prev)" = "v2026.07.09-10" ] || fail "-10 > -2 via sort -V"
[ -z "$(printf 'phase-p1\n' | select_prev)" ] || fail "no calver -> empty"
[ -z "$(printf '' | select_prev)" ] || fail "empty input -> empty"

echo "all pass"
