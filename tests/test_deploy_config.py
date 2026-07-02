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
