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
    job_count = 0
    for raw in _CRON.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # env-assignment lines (UV=..., LOCK=...) don't match a cron schedule,
        # so _SCHED skips them; only real job lines are asserted.
        if _SCHED.match(line):
            job_count += 1
            assert "flock -n" in line, f"cron job not flock -n wrapped: {line!r}"
    # Guards against a vacuous pass (all job lines deleted) and against the
    # count silently dropping below the 4 jobs the crontab defines.
    assert job_count >= 4, f"expected >= 4 cron jobs, found {job_count}"


def test_cn02_crontab_path_includes_local_bin():
    # Regression guard: upload_db_to_drive.py calls a bare `rclone` (at
    # ~/.local/bin/rclone) via subprocess; cron's default PATH omits that dir,
    # so the managed crontab must set PATH to include it or the Drive backup
    # breaks silently on the first managed run.
    for raw in _CRON.read_text(encoding="utf-8").splitlines():
        if raw.startswith("PATH="):
            assert "/home/reidlin/.local/bin" in raw, f"PATH must include ~/.local/bin: {raw!r}"
            break
    else:
        raise AssertionError("cn02.crontab must set PATH (cron default omits ~/.local/bin)")


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


_SH = _ROOT / "scripts" / "deploy.sh"


def test_deploy_sh_critical_invariants():
    sh = _SH.read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in sh, "-E (errtrace) required so ERR trap fires inside main()"
    # flock must be acquired before git pull (else a cron job can start mid-pull/sync)
    assert sh.index("flock -w 300 9") < sh.index("git pull"), "flock must precede git pull"
    assert "TWSTOCK_DB_PATH=:memory:" in sh and 'pytest -m "not slow"' in sh, "deterministic smoke, exclude prod-DB slow bench"
    assert "--frozen --extra dev" in sh, "dev extra installs pytest"
