# GitHub Actions 部署 cn02 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** merge → master 時,cn02 上的 self-hosted runner 以隔離使用者執行 scoped `deploy.sh`(pull → uv sync → smoke → crontab),失敗走獨立 stdlib 通知器發 Telegram。

**Architecture:** GitHub Actions workflow(`push: master`,`runs-on: [self-hosted, cn02]`,`permissions: {}`,無第三方 action)以低權限 `ghrunner` 執行,經 sudoers 只准 `sudo -H -u reidlin deploy.sh`。部署邏輯全在 trigger-agnostic 的 `deploy.sh`(flock 序列化、`set -Eeuo pipefail`)。失敗通知由獨立、stdlib-only、跑在 system python3 的 `notify_deploy.py` 負責,與專案 venv 完全脫鉤。

**Tech Stack:** GitHub Actions self-hosted runner、bash、Python 3 標準庫(urllib/socket)、uv、pytest。

**Spec:** `docs/superpowers/specs/2026-07-02-github-action-deploy-cn02-design.md`
**Branch:** `feat/deploy-cn02`(已建立)

## Global Constraints

- cn02 **只能 outbound**;通知走 `notify.py` 同款 DoH-pinned Telegram(但獨立 stdlib 版)。
- Repo 是 **PUBLIC** → runner 以**專用低權限使用者 `ghrunner`** 執行,經 sudoers 只准一條無參數命令 `/home/reidlin/stock/scripts/deploy.sh`;`deploy.sh` 由 `reidlin` 擁有、`ghrunner` **不可寫**。
- deploy.yml:`permissions: {}`、**無任何 `uses:`(零第三方 action)**、`on: push: branches: [master]`、`runs-on: [self-hosted, cn02]`、`concurrency: {group: deploy-cn02, cancel-in-progress: false}`、`timeout-minutes: 15`。
- `deploy.sh`:`set -Eeuo pipefail`(`-E` 讓 ERR trap 傳入 function);flock **從 `git pull` 前**上鎖涵蓋整段;smoke = `TWSTOCK_DB_PATH=:memory: uv run pytest -m "not slow"`;`uv sync --frozen --extra dev`(pytest 在 `[optional-dependencies].dev`)。
- `notify_deploy.py`:**stdlib-only、零專案 import、跑 system python3**;自己解析 `--env-file` 取 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`;去重 marker `~/.deploy-notify/<date>-<sha>`;送失敗 → stderr + spool,**不寫 `notification_log`**。
- `cn02.crontab`:sentinel header `# MANAGED-BY: repo scripts/cn02.crontab`;每條 job 包同一把 flock;reidlin 路徑。
- 部署不動 DB/`data/`/`.env` 內容。測試不得觸網(全 mock)。
- Repo owner = `@yuka1981`。

## 檔案結構

- `scripts/notify_deploy.py`(新)— 獨立 stdlib 失敗通知器。
- `tests/test_notify_deploy.py`(新)— 通知器單元測試(mock 網路)。
- `scripts/deploy.sh`(新)— 部署編排(trigger-agnostic)。
- `scripts/cn02.crontab`(新)— 受管 crontab(每條持鎖)。
- `tests/test_deploy_config.py`(新)— cn02.crontab + deploy.yml 的靜態不變式檢查(純文字,無 yaml 依賴)。
- `.github/workflows/deploy.yml`(新)。
- `.github/CODEOWNERS`(新)。
- `scripts/twstock-screener.cron`(改)— 標註 deprecated。
- `docs/superpowers/runbooks/2026-07-02-deploy-cn02-rollout.md`(新)— cn02 手動佈建 runbook。

---

### Task 1: `notify_deploy.py` — 獨立 stdlib 失敗通知器

**Files:**
- Create: `scripts/notify_deploy.py`
- Test: `tests/test_notify_deploy.py`

**Interfaces:**
- Produces(供 `deploy.sh` 呼叫):CLI `python3 scripts/notify_deploy.py --env-file <path> --sha <sha> --message <text> [--today <iso>]`;退出碼 0=已送或已去重,1=設定缺失或送失敗。
- Produces(供測試):`parse_env_file(path)->dict`、`doh_resolve(host,timeout=5.0)->str|None`、`pinned_dns(host,ip)` ctxmgr、`post_telegram(token,chat_id,message,timeout=15.0)->bool`、`main(argv=None)->int`;模組級 `NOTIFY_DIR`、`SPOOL`(測試會 monkeypatch)。

- [ ] **Step 1: 寫失敗測試**

`tests/test_notify_deploy.py`(用 importlib 以檔案路徑載入 scripts 模組——scripts/ 非 package):

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parent.parent / "scripts" / "notify_deploy.py"


def _load():
    spec = importlib.util.spec_from_file_location("notify_deploy", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def nd(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "NOTIFY_DIR", tmp_path / "notify")
    monkeypatch.setattr(mod, "SPOOL", tmp_path / "notify" / "spool.log")
    return mod


def _envfile(tmp_path, token="123:abc", chat="42"):
    p = tmp_path / ".env"
    p.write_text(
        f'TELEGRAM_BOT_TOKEN="{token}"\n'
        f"TELEGRAM_CHAT_ID={chat}\n"
        "# comment\n\nOTHER=ignored\n",
        encoding="utf-8",
    )
    return p


def test_parse_env_file_strips_quotes_and_comments(nd, tmp_path):
    env = nd.parse_env_file(_envfile(tmp_path))
    assert env["TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert env["TELEGRAM_CHAT_ID"] == "42"
    assert "OTHER" in env and "#" not in "".join(env.keys())


def test_send_success_marks_and_returns_zero(nd, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nd, "post_telegram", lambda t, c, m: calls.append((t, c, m)) or True)
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "abc123",
        "--message", "boom", "--today", "2026-07-02",
    ])
    assert rc == 0
    assert calls == [("123:abc", "42", "boom")]
    assert (nd.NOTIFY_DIR / "2026-07-02-abc123").exists()  # marker written


def test_dedup_same_date_sha_skips_send(nd, tmp_path, monkeypatch):
    (nd.NOTIFY_DIR).mkdir(parents=True)
    (nd.NOTIFY_DIR / "2026-07-02-abc123").write_text("", encoding="utf-8")
    monkeypatch.setattr(nd, "post_telegram", lambda *a: pytest.fail("should not send"))
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "abc123",
        "--message", "boom", "--today", "2026-07-02",
    ])
    assert rc == 0


def test_send_failure_spools_and_no_marker(nd, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nd, "post_telegram", lambda *a: False)
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "def456",
        "--message", "boom", "--today", "2026-07-02",
    ])
    assert rc == 1
    assert not (nd.NOTIFY_DIR / "2026-07-02-def456").exists()  # retry can alert
    assert "boom" in nd.SPOOL.read_text(encoding="utf-8")
    assert "FAILED" in capsys.readouterr().err


def test_missing_config_returns_one(nd, tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    rc = nd.main(["--env-file", str(empty), "--sha", "x", "--message", "m", "--today", "2026-07-02"])
    assert rc == 1


def test_post_telegram_builds_request_without_network(nd, monkeypatch):
    seen = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["data"] = req.data
        return _Resp()

    monkeypatch.setattr(nd, "doh_resolve", lambda host, timeout=5.0: None)  # skip DoH
    monkeypatch.setattr(nd.urllib.request, "urlopen", fake_urlopen)
    ok = nd.post_telegram("123:abc", "42", "hello world")
    assert ok is True
    assert "/bot123:abc/sendMessage" in seen["url"]
    assert b"chat_id=42" in seen["data"] and b"hello" in seen["data"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_notify_deploy.py -q`
Expected: FAIL(`FileNotFoundError`/`spec.loader` 無法載入——`scripts/notify_deploy.py` 尚不存在)

- [ ] **Step 3: 實作 `scripts/notify_deploy.py`**

```python
#!/usr/bin/env python3
"""Standalone deploy-failure notifier — STDLIB ONLY, zero project imports.

Runs on SYSTEM python3 so a broken project venv (e.g. failed `uv sync`)
cannot prevent the alert. Mirrors twstock_screener.notify's DoH-pinned
Telegram path but with urllib instead of httpx. Sends PLAIN text (no
parse_mode) so no MarkdownV2 escaping is needed. Dedup: one alert per
(date, sha) via a marker file. Config from --env-file (.env). On send
failure: loud stderr + spool append; caller still exits non-zero.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

DOH_URL = "https://1.1.1.1/dns-query"
TELEGRAM_HOST = "api.telegram.org"
NOTIFY_DIR = Path.home() / ".deploy-notify"
SPOOL = NOTIFY_DIR / "spool.log"


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE, ignores blanks/#comments, strips
    surrounding quotes). No pydantic / project dependency."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def doh_resolve(host: str, timeout: float = 5.0) -> str | None:
    """One A record via Cloudflare DoH-over-IP; None on any failure."""
    q = urllib.parse.urlencode({"name": host, "type": "A"})
    req = urllib.request.Request(
        f"{DOH_URL}?{q}", headers={"accept": "application/dns-json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for ans in data.get("Answer", []):
            if ans.get("type") == 1 and ans.get("data"):
                return str(ans["data"])
    except Exception:
        return None
    return None


@contextlib.contextmanager
def pinned_dns(host: str, ip: str):
    """Redirect getaddrinfo(host) to a pinned IP for the block; TLS still
    uses the hostname so SNI + cert verification stay correct."""
    orig = socket.getaddrinfo

    def patched(h, *a, **k):
        return orig(ip if h == host else h, *a, **k)

    socket.getaddrinfo = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = orig  # type: ignore[assignment]


def post_telegram(token: str, chat_id: str, message: str, timeout: float = 15.0) -> bool:
    """POST plain text (no parse_mode). DoH-pinned when possible."""
    url = f"https://{TELEGRAM_HOST}/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    ip = doh_resolve(TELEGRAM_HOST)
    ctx = pinned_dns(TELEGRAM_HOST, ip) if ip else contextlib.nullcontext()
    try:
        with ctx:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
    except Exception:
        return False


def spool_failure(message: str) -> None:
    NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
    with SPOOL.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--today", default=date.today().isoformat())  # test seam
    args = p.parse_args(argv)

    marker = NOTIFY_DIR / f"{args.today}-{args.sha}"
    if marker.exists():
        return 0  # dedup: this (date, sha) already alerted

    env = parse_env_file(Path(args.env_file))
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        sys.stderr.write("DEPLOY-NOTIFY: missing TELEGRAM_BOT_TOKEN/CHAT_ID\n")
        spool_failure(f"[no-config] {args.message}")
        return 1

    if post_telegram(token, chat, args.message):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
        return 0
    sys.stderr.write(f"DEPLOY-NOTIFY: telegram send FAILED — {args.message}\n")
    spool_failure(args.message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_notify_deploy.py -v`
Expected: PASS(6 個測試全綠)

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/notify_deploy.py
git add scripts/notify_deploy.py tests/test_notify_deploy.py
git commit -m "feat: standalone stdlib deploy-failure notifier (venv-decoupled)"
```

---

### Task 2: `deploy.sh` + `cn02.crontab` + 靜態不變式測試

**Files:**
- Create: `scripts/deploy.sh`, `scripts/cn02.crontab`
- Test: `tests/test_deploy_config.py`

**Interfaces:**
- Consumes: `scripts/notify_deploy.py`(Task 1)——deploy.sh 的 ERR trap 呼叫它。
- Produces: `scripts/deploy.sh`(冪等、flock 序列化的部署入口,供 workflow 與 §7 polling 共用);`scripts/cn02.crontab`(含 sentinel、每條持鎖)。

- [ ] **Step 1: 寫失敗測試**

`tests/test_deploy_config.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CRON = _ROOT / "scripts" / "cn02.crontab"

# cron schedule line: 5 time fields then a command (allow @monthly etc. too)
_SCHED = re.compile(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+|@\w+)\s+\S")


def test_cn02_crontab_has_managed_sentinel():
    text = _CRON.read_text(encoding="utf-8")
    assert text.startswith("# MANAGED-BY: repo scripts/cn02.crontab"), (
        "deploy.sh install_crontab greps this exact sentinel; must be line 1"
    )


def test_every_cron_job_is_flock_wrapped():
    for raw in _CRON.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # env-assignment lines (UV=..., LOCK=...) don't match a cron schedule,
        # so _SCHED skips them; only real job lines are asserted.
        if _SCHED.match(line):
            assert "flock" in line, f"cron job not flock-wrapped: {line!r}"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_deploy_config.py -q`
Expected: FAIL(`FileNotFoundError`——`scripts/cn02.crontab` 尚不存在)

- [ ] **Step 3a: 實作 `scripts/deploy.sh`**

```bash
#!/usr/bin/env bash
set -Eeuo pipefail        # -E: ERR trap must reach inside main()/functions

PROJECT="$HOME/stock"
UV="$HOME/.local/bin/uv"
LOCK="$PROJECT/.deploy.lock"
CURRENT_STEP="init"

notify_failure() {
  local sha
  sha=$(git -C "$PROJECT" rev-parse --short HEAD 2>/dev/null || echo unknown)
  python3 "$PROJECT/scripts/notify_deploy.py" \
    --env-file "$PROJECT/.env" \
    --sha "$sha" \
    --message "🚨 DEPLOY FAILED on cn02 @ ${sha} — step: ${CURRENT_STEP}" \
    || echo "DEPLOY-NOTIFY-FAILED (see ~/.deploy-notify/spool.log) step=${CURRENT_STEP}" >&2
}
trap notify_failure ERR

install_crontab() {
  local f="$PROJECT/scripts/cn02.crontab"
  test -s "$f"
  grep -q '^# MANAGED-BY: repo scripts/cn02.crontab' "$f"
  crontab -l > "$HOME/.crontab.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  crontab "$f"
}

main() {
  cd "$PROJECT"
  exec 9>"$LOCK"
  CURRENT_STEP="lock"
  flock -w 300 9 || return 1     # covers pull→sync→smoke→crontab; cron entries share this lock

  CURRENT_STEP="git pull";  git pull --ff-only origin master
  CURRENT_STEP="syntax";    bash -n "$PROJECT/scripts/deploy.sh"
  CURRENT_STEP="uv sync";   "$UV" sync --frozen --extra dev
  CURRENT_STEP="smoke";     TWSTOCK_DB_PATH=:memory: "$UV" run pytest -m "not slow" -q
  CURRENT_STEP="crontab";   install_crontab
}

main "$@"
```

- [ ] **Step 3b: 實作 `scripts/cn02.crontab`**

(sentinel 必為第 1 行;每條 job 包同一把 flock;譯自 `scripts/twstock-screener.cron` 的 reidlin 版。`flock -n` = 部署進行中就跳過本次 job,不排隊堆積。)

```
# MANAGED-BY: repo scripts/cn02.crontab
# reidlin@s5xq-cn02 per-user crontab. Sole source of truth; deploy.sh
# overwrites the live crontab with this file. Every job shares $LOCK with
# deploy.sh so no job starts while a deploy mutates the venv/checkout.
SHELL=/bin/bash
UV=/home/reidlin/.local/bin/uv
LOCK=/home/reidlin/stock/.deploy.lock

# Monthly metadata refresh (stocks list + holidays)
0  2 1 * *   flock -n $LOCK -c 'cd ~/stock && $UV run python scripts/refresh_metadata.py >> logs/metadata.log 2>&1'

# Daily fetch (03:00, weekdays)
0  3 * * 1-5 flock -n $LOCK -c 'cd ~/stock && $UV run python scripts/backfill.py --days 5 >> logs/fetch.log 2>&1'

# Daily DB backup to Google Drive (03:30, weekdays)
30 3 * * 1-5 flock -n $LOCK -c 'cd ~/stock && $UV run python scripts/upload_db_to_drive.py >> logs/drive_backup.log 2>&1'

# Daily analyze + Telegram (08:20, weekdays)
20 8 * * 1-5 flock -n $LOCK -c 'cd ~/stock && $UV run python scripts/analyze.py >> logs/analyze.log 2>&1'
```

- [ ] **Step 4: 跑測試 + bash 語法檢查**

Run: `uv run pytest tests/test_deploy_config.py -q && bash -n scripts/deploy.sh && echo OK`
Expected: PASS(2 個測試)+ `bash -n` 無輸出 + `OK`。若已裝 shellcheck,額外跑 `shellcheck scripts/deploy.sh`(非強制)。

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/deploy.sh
git add scripts/deploy.sh scripts/cn02.crontab tests/test_deploy_config.py
git commit -m "feat: cn02 deploy.sh (flock-serialized) + managed crontab"
```

---

### Task 3: `deploy.yml` + `CODEOWNERS` + deprecate 舊 cron + workflow 不變式測試

**Files:**
- Create: `.github/workflows/deploy.yml`, `.github/CODEOWNERS`
- Modify: `scripts/twstock-screener.cron`(標 deprecated)
- Test: `tests/test_deploy_config.py`(追加 deploy.yml 檢查)

**Interfaces:**
- Consumes: `scripts/deploy.sh`(Task 2)——workflow 以 `sudo -H -u reidlin` 呼叫它。

- [ ] **Step 1: 追加失敗測試**

在 `tests/test_deploy_config.py` 追加(純文字斷言,鎖死安全姿態,無需 yaml 依賴):

```python
_WF = _ROOT / ".github" / "workflows" / "deploy.yml"


def test_deploy_workflow_security_invariants():
    text = _WF.read_text(encoding="utf-8")
    assert "permissions: {}" in text, "workflow must drop the GITHUB_TOKEN"
    assert "uses:" not in text, "no third-party actions on the self-hosted runner"
    assert "[self-hosted, cn02]" in text, "must pin the cn02 self-hosted runner"
    assert "cancel-in-progress: false" in text
    # trigger is push:master only — must not react to pull_request
    assert "pull_request" not in text
    assert "branches: [master]" in text
    # scoped invocation as reidlin (not running as the runner user)
    assert "sudo -H -u reidlin" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_deploy_config.py::test_deploy_workflow_security_invariants -q`
Expected: FAIL(`FileNotFoundError`——`.github/workflows/deploy.yml` 尚不存在)

- [ ] **Step 3a: 實作 `.github/workflows/deploy.yml`**

```yaml
name: deploy-cn02
on:
  push:
    branches: [master]
permissions: {}
concurrency:
  group: deploy-cn02
  cancel-in-progress: false
jobs:
  deploy:
    runs-on: [self-hosted, cn02]
    timeout-minutes: 15
    steps:
      - name: Run scoped deploy as reidlin
        # -H sets $HOME=/home/reidlin so deploy.sh's $HOME/stock resolves.
        # No actions/checkout: deploy.sh pulls the production checkout itself.
        run: sudo -H -u reidlin /home/reidlin/stock/scripts/deploy.sh
```

- [ ] **Step 3b: 實作 `.github/CODEOWNERS`**

```
# Deploy machinery runs on the cn02 production host — require owner review.
# Enforced by the all-branches ruleset (see rollout runbook).
/.github/workflows/    @yuka1981
/.github/CODEOWNERS    @yuka1981
/scripts/deploy.sh     @yuka1981
/scripts/cn02.crontab  @yuka1981
```

- [ ] **Step 3c: 標 `scripts/twstock-screener.cron` deprecated**

把該檔第一行(`# /etc/cron.d/twstock-screener`)改為:

```
# DEPRECATED — superseded by scripts/cn02.crontab (reidlin per-user, flock-wrapped).
# Kept for historical reference of the original /etc/cron.d layout.
# /etc/cron.d/twstock-screener
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_deploy_config.py -q`
Expected: PASS(3 個測試:sentinel、flock-wrap、workflow invariants)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml .github/CODEOWNERS scripts/twstock-screener.cron tests/test_deploy_config.py
git commit -m "feat: deploy-cn02 workflow (permissions:{}, no third-party actions) + CODEOWNERS"
```

---

### Task 4: cn02 佈建 runbook(手動步驟文件化)

**Files:**
- Create: `docs/superpowers/runbooks/2026-07-02-deploy-cn02-rollout.md`

**Interfaces:** 無程式介面;交付物是可照做的手動 runbook。內容須完整覆蓋 spec §1/§2/§8 的每一步。

- [ ] **Step 1: 撰寫 runbook**

寫入 `docs/superpowers/runbooks/2026-07-02-deploy-cn02-rollout.md`,包含以下小節(每節給實際命令):

1. **前置驗證(gate)**:在 cn02 以臨時方式 `./run.sh` 跑 runner,確認能連 GitHub broker 端點。**不通 → 停,改用 spec §7 polling(改 cn02 crontab 每 10 分鐘 `flock $LOCK -c 'git fetch && [ 版本前進 ] && scripts/deploy.sh'`),其餘檔案不變。**
2. **建立隔離使用者**:`sudo useradd -m -s /bin/bash ghrunner`。
3. **sudoers**:寫 `/etc/sudoers.d/ghrunner-deploy`:
   `ghrunner ALL=(reidlin) NOPASSWD: /home/reidlin/stock/scripts/deploy.sh`;`visudo -c` 驗證。強調不變式:`deploy.sh` 由 reidlin 擁有、ghrunner 不可寫(`ls -l`);只准這一條無參數命令。
4. **裝 runner**:以 `ghrunner` 於 `~ghrunner/actions-runner` 註冊 repo-level runner,labels `self-hosted,cn02`;systemd service(`svc.sh install ghrunner` 或 system unit 指定 `User=ghrunner`;若走 user service 則 `loginctl enable-linger ghrunner`)。
5. **crontab 遷移(關鍵)**:
   - 先 `crontab -u reidlin -l` 快照 cn02 現況,逐條核對進 `scripts/cn02.crontab`(補上非本專案的既有條目、確認路徑),commit 更新。
   - **首次 rollout 靜默窗**:現行 crontab 各條尚未持鎖 → 選一個無 cron job 執行的時段,或先手動 `crontab scripts/cn02.crontab` 裝上持鎖版,之後自動部署才安全。
   - 文件化:臨時手動跑 repo 碼者也應 `flock $LOCK` 包起來。
6. **GitHub 設定**:
   - Repo ruleset(**all branches**):require PR + review;restrict file paths `/.github/workflows/**`、`/scripts/deploy.sh`、`/scripts/cn02.crontab`(靠 CODEOWNERS review);restrict branch creation。
   - Actions → Runners:確認 runner online;(選配)若遷 one-person org,設 runner group 只允許 `deploy.yml`。
   - 記錄殘餘風險(spec §1 末)。
7. **端到端驗證(spec §8)**:
   - 首次真 merge → Actions run 全綠 + cn02 `git -C ~reidlin/stock log -1` 為 merge SHA + 檔案由 reidlin 更新。
   - 失敗演練:cn02 上手動製造必敗 smoke 執行 `sudo -H -u reidlin scripts/deploy.sh`,確認 Telegram 收到 `DEPLOY FAILED @ <sha> — step: smoke`,再驗通知器本身失敗時 stderr + `~/.deploy-notify/spool.log` 有痕跡;驗完復原。
   - 權限驗證:以 ghrunner 試 `sudo -u reidlin bash -c id` 應被拒;`cat ~reidlin/.env` 應被拒;`test -w scripts/deploy.sh`(ghrunner)應為否。

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/runbooks/2026-07-02-deploy-cn02-rollout.md
git commit -m "docs: cn02 deploy rollout runbook (runner user isolation, rulesets, e2e verify)"
```

---

## Self-Review

**1. Spec coverage:**
- §1 觸發+安全(使用者隔離/sudoers/permissions:{}/無第三方 action/rulesets/CODEOWNERS)→ Task 3(deploy.yml + CODEOWNERS + invariant 測試)+ Task 4(sudoers/ruleset/runner-group 手動步驟)✓
- §2 佈建 → Task 4 runbook ✓
- §3 deploy.sh(set -Eeuo/flock 從 pull 前/bash -n/uv sync --extra dev/smoke :memory: not slow/crontab 護欄)→ Task 2 ✓
- §4 notify_deploy.py(stdlib/自讀 .env/date+sha marker/失敗 spool 不遮蔽)→ Task 1 ✓
- §5 cn02.crontab(sentinel/每條 flock/遷移/首次窗)→ Task 2(檔+測試)+ Task 4(遷移步驟)✓
- §6 deploy.yml(sudo -H/無 checkout/concurrency/timeout)→ Task 3 ✓
- §7 polling 回退 → Task 4 §1 gate 失敗分支 ✓
- §8 驗證 → Task 4 §7 + 各任務本地測試 ✓
- §9 不做 → 未實作回滾/hosted CI/多環境/ephemeral 強制,plan 未加這些 ✓

**2. Placeholder scan:** 無 TBD/TODO;每個 code step 皆含完整程式碼與測試碼;runbook step 給實際命令方向而非空泛佔位。

**3. Type/介面一致:** `notify_deploy.py` 的 CLI(`--env-file/--sha/--message/--today`)在 Task 1 定義、Task 2 deploy.sh 呼叫一致;`main/parse_env_file/doh_resolve/pinned_dns/post_telegram/NOTIFY_DIR/SPOOL` 測試與實作一致;sentinel 字串 `# MANAGED-BY: repo scripts/cn02.crontab` 在 cn02.crontab(Task 2)、deploy.sh grep(Task 2)、測試(Task 2)三處逐字一致;`$LOCK` 路徑 `~/stock/.deploy.lock` 在 deploy.sh 與 cn02.crontab 一致;deploy.yml 的 `sudo -H -u reidlin .../deploy.sh` 與 sudoers(Task 4)逐字一致。
