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
