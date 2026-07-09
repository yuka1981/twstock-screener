# Auto-Release on Master Merge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every push to `master`, automatically mint a date-CalVer tag and create a GitHub Release with native auto-generated notes.

**Architecture:** A standalone `.github/workflows/release.yml` (GitHub-hosted `ubuntu-latest`, `contents: write`) sources pure shell helpers from `scripts/release_tag.sh` to compute the tag and the notes-start boundary, then calls `gh release create`. The tag logic is unit-tested offline via `scripts/tests/test_release_tag.sh`, which the workflow also runs as a preflight gate before touching any release. Fully independent of the existing cn02 deploy workflow.

**Tech Stack:** GitHub Actions, `bash` (`-euo pipefail`), `gh` CLI (preinstalled on GitHub-hosted runners), coreutils (`sed`, `sort -V`, `git`).

## Global Constraints

- Branch for this work: `docs/auto-release-spec` (already holds the spec commit; implementation commits continue on it).
- Commit messages: Conventional Commits (`feat:`/`test:`/`ci:`/`docs:` …).
- Tag format: `v<YYYY.MM.DD>` computed in `TZ=Asia/Taipei`; same-day collisions append `-2`, `-3`, …
- Do NOT modify `.github/workflows/deploy.yml`, `pyproject.toml`, or `scripts/cn02.crontab`. No `CHANGELOG.md`.
- The release workflow must run on GitHub-hosted `ubuntu-latest`, NOT the self-hosted `cn02` runner (keep the write token off production).
- Shell helpers are pure: no `git`/`gh`/network calls inside `scripts/release_tag.sh` — data is fed in via stdin/args.
- Source of truth for all code below: `docs/superpowers/specs/2026-07-09-auto-release-on-master-design.md` (Codex-reviewed, verdict PASS). The shell in Tasks 1 was run locally at design time and all asserts passed.
- All `Run:` commands below are executed from the **repo root** (`/home/reid/stock`); every path in this plan is relative to it.
- `scripts/tests/` does not exist yet — it is created in Task 1 Step 1.

## File Structure

- `scripts/release_tag.sh` — pure functions `compute_tag` (CalVer + same-day suffix) and `select_prev` (previous published CalVer tag for the notes boundary). No side effects.
- `scripts/tests/test_release_tag.sh` — offline self-test: sources the helper, asserts `compute_tag`/`select_prev` behavior. Runnable via `bash`.
- `.github/workflows/release.yml` — the workflow: checkout → preflight self-test → compute tag + notes-start → `gh release create`.

---

### Task 1: Pure tag helpers + offline self-test

**Files:**
- Create: `scripts/release_tag.sh`
- Create (test): `scripts/tests/test_release_tag.sh`

**Interfaces:**
- Produces: `compute_tag <today:YYYY.MM.DD>` — reads existing tag names (one per line) on **stdin**, echoes the chosen tag (`v<today>` or `v<today>-N`) on stdout.
- Produces: `select_prev` — reads published release tag names (one per line) on **stdin**, echoes the newest CalVer tag (`sort -V`) or empty string on stdout.
- Consumes: nothing (first task).

- [ ] **Step 1: Write the failing test**

First create the directory (it does not exist yet): `mkdir -p scripts/tests`

Then create `scripts/tests/test_release_tag.sh`:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/tests/test_release_tag.sh`
Expected: FAIL — `release_tag.sh` does not exist yet, so `source` errors with `No such file or directory` (non-zero exit).

- [ ] **Step 3: Write minimal implementation**

Create `scripts/release_tag.sh`:

```bash
#!/usr/bin/env bash
# 純字串運算，供 workflow source 與離線自測共用。餵資料進來，不自己碰 git/gh。

# compute_tag <today: YYYY.MM.DD>；既有 tag 由 stdin 逐行餵入。
# 回傳 v<date>，若已存在則跳 -2/-3…（精確整行比對，避免 v2026.07.09foo 誤中）。
compute_tag() {
  local base="v$1" tag existing n=1
  existing="$(cat)"
  tag="$base"
  while printf '%s\n' "$existing" | grep -qxF "$tag"; do
    n=$((n + 1)); tag="$base-$n"
  done
  printf '%s\n' "$tag"
}

# select_prev；已發布 release 的 tag 名由 stdin 逐行餵入。
# 回傳「上一個 CalVer release」tag（version 排序取最新），無則空字串。
# 用 sed -n：無匹配自然 0 退出（不需 || true），故 sort/tail 的真實失敗會照常傳播 → fail-closed。
select_prev() {
  sed -nE '/^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}(-[0-9]+)?$/p' | sort -V | tail -n1
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/tests/test_release_tag.sh`
Expected: PASS — prints `all pass`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/release_tag.sh scripts/tests/test_release_tag.sh
git commit -m "feat: pure CalVer tag helpers with offline self-test"
```

---

### Task 2: Release workflow

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `scripts/release_tag.sh` (`compute_tag`, `select_prev`) and `scripts/tests/test_release_tag.sh` from Task 1, both at repo root after checkout.
- Produces: a GitHub Release + tag per push to `master` (no downstream consumers).

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release.yml`:

```yaml
name: release
on:
  push: { branches: [master] }          # 僅 master；無 workflow_dispatch
concurrency: { group: release, queue: max }   # FIFO 排隊、不丟棄 pending run
permissions: { contents: write }
jobs:
  release:
    runs-on: ubuntu-latest                      # 刻意用 hosted，write token 不碰 cn02
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0, fetch-tags: true }
      - env: { GH_TOKEN: "${{ github.token }}" }
        shell: bash
        run: |
          set -euo pipefail
          source scripts/release_tag.sh

          # 先自我把關：邏輯回歸就在動到 release 之前紅燈
          bash scripts/tests/test_release_tag.sh

          today="$(TZ=Asia/Taipei date +%Y.%m.%d)"
          existing="$(git tag --list 'v*')"          # 簡單賦值：git 失敗 → set -e 中止
          TAG="$(printf '%s\n' "$existing" | compute_tag "$today")"

          # capture-first fail-closed：gh 失敗發生在簡單賦值上 → set -e 中止整個 run。
          released="$(gh release list --limit 100 --json tagName,isDraft \
                        --jq '.[] | select(.isDraft==false) | .tagName')"
          NOTES_START="$(printf '%s\n' "$released" | select_prev)"

          args=(--target "$GITHUB_SHA" --title "$TAG" --generate-notes --fail-on-no-commits)
          [ -n "$NOTES_START" ] && args+=(--notes-start-tag "$NOTES_START")
          gh release create "$TAG" "${args[@]}"
```

- [ ] **Step 2: Validate the workflow YAML**

Prefer `actionlint` if available (validates Actions schema, not just YAML):

Run: `actionlint .github/workflows/release.yml`
Expected: no output, exit 0.

Fall back to the syntax parse below if EITHER `actionlint` is not installed OR an installed `actionlint` errors specifically on `concurrency.queue` (older binaries predate that key — that error is a false positive, not a real problem; `queue: max` is valid current Actions syntax). The parse note: `on:` parses as the boolean key `True` under YAML 1.1 — that is expected and harmless; we only confirm it parses:

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('yaml ok')"`
Expected: prints `yaml ok`, exit 0. (If PyYAML is unavailable, install it in a throwaway env — `uv run --with pyyaml python3 -c "..."` — or skip and rely on GitHub's parser on push.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: auto-release tag+notes on push to master"
```

---

## Post-implementation (live validation — per spec, not a plan task)

The first true end-to-end proof happens when this branch merges to `master`: that merge's push triggers the newly-added workflow (GitHub uses the workflow file from the event's own commit), producing the first `v<date>` release. Inspect its tag name and notes. A first-run failure is a harmless red run — `deploy.yml` is unaffected and no junk release is left (a failed `gh release create` before tag creation leaves nothing). See the spec's 〈驗證計畫〉 and 〈已知限制〉.

## Self-Review

**1. Spec coverage:**
- CalVer tag + same-day suffix → Task 1 `compute_tag` + Task 2 `TAG=`. ✓
- Native auto-generated notes → Task 2 `--generate-notes`. ✓
- Notes boundary from previous published CalVer release → Task 1 `select_prev` + Task 2 `--notes-start-tag`. ✓
- `queue: max`, `permissions: contents: write`, `ubuntu-latest`, `shell: bash` → Task 2 YAML. ✓
- `--fail-on-no-commits` idempotency, capture-first fail-closed, preflight self-test → Task 2. ✓
- Executable verification contract (named helper + named test + exact invocation) → Tasks 1 & 2. ✓
- No `workflow_dispatch`; don't touch deploy/pyproject → honored (Global Constraints + YAML). ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"; all code is complete and was run at design time. ✓

**3. Type consistency:** `compute_tag`/`select_prev` names and their stdin/stdout contracts are identical across Task 1 (definition + test) and Task 2 (workflow usage). ✓
