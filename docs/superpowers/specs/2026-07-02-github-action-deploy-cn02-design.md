# GitHub Actions 部署 s5xq-cn02(merge → master 觸發)

**日期**: 2026-07-02
**範圍**: `.github/workflows/deploy.yml` + `scripts/deploy.sh` + `scripts/cn02.crontab` + `scripts/notify_deploy.py`;另含 cn02 一次性 runner 佈建(手動步驟,文件化)

## 背景與動機

生產 pipeline 跑在 s5xq-cn02(user `reidlin`,per-user crontab,uv 在
`~/.local/bin`)。目前 merge 到 master 後要手動上 cn02 `git pull`,容易忘、
無驗證、無通知。目標:merge → master 自動部署到 cn02,失敗走既有 Telegram
管道通知。

## 關鍵約束(已確認)

- **cn02 只能 outbound**:無公網 SSH 可達 → push 型(hosted runner SSH 進來)
  不可行。選定 **self-hosted runner**(outbound long-poll,NAT 後可用)。
- **Repo 是 PUBLIC**:self-hosted runner 安全姿態是設計重點(見 §1)。
- **部署範圍**:git pull + uv sync + smoke test + crontab 同步。不碰
  DB/`data/`/`.env`。
- **失敗通知**:重用 `notify.py` 的 DoH relay Telegram 管道。成功靜默
  (GitHub 綠勾即回執)。

## 1. 觸發與安全

- `deploy.yml` 只在 `push: branches: [master]` 觸發。fork PR 無法產生 push
  事件;只有具寫權限者 merge 才觸發。
- **self-hosted runner 只服務 deploy.yml 這一個 workflow**。未來任何
  `pull_request` CI 一律 `runs-on: ubuntu-latest`,外部程式碼永不落在 cn02。
- 一次性 repo 設定(手動):Settings → Actions → General →
  「Require approval for all outside collaborators」。
- Runner 註冊為 repo-level,labels `[self-hosted, cn02]`;workflow
  `runs-on: [self-hosted, cn02]` 雙 label 指定,防未來多 runner 誤中。

## 2. cn02 端 runner 佈建(一次性,手動,文件化在 plan)

- 以 `reidlin` 裝 GitHub Actions runner 至 `~/actions-runner`。
- **前置驗證**:先手動 `./run.sh` 確認 runner 能連 GitHub broker 端點
  (cn02 網路特殊——Telegram 要走 DoH relay;git 可用不代表 runner 端點可用。
  驗證通過才裝 service;不通則此架構止步,回退 pull 型輪詢,見 §7)。
- systemd **user** service + `loginctl enable-linger reidlin`(開機自啟;
  `sudo` 僅 enable-linger 一次)。

## 3. `scripts/deploy.sh`(部署邏輯唯一真相)

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT="$HOME/stock"          # cn02 生產目錄(非 runner _work checkout)
UV="$HOME/.local/bin/uv"

notify_failure() {
  local sha msg
  sha=$(git -C "$PROJECT" rev-parse --short HEAD 2>/dev/null || echo unknown)
  msg="🚨 DEPLOY FAILED on cn02 @ ${sha} — step: ${CURRENT_STEP:-unknown}"
  "$UV" run --project "$PROJECT" python "$PROJECT/scripts/notify_deploy.py" "$msg" || true
}
trap notify_failure ERR

main() {
  cd "$PROJECT"
  CURRENT_STEP="git pull";    git pull --ff-only origin master
  CURRENT_STEP="uv sync";     "$UV" sync --frozen
  CURRENT_STEP="smoke test";  "$UV" run pytest -q
  CURRENT_STEP="crontab";     crontab "$PROJECT/scripts/cn02.crontab"
}
main "$@"
```

設計要點:

- **自更新防護**:全腳本包 `main(){...}; main "$@"` —— bash 先 parse 完整檔
  才執行,`git pull` 中途替換腳本本體不會執行錯亂。已知特性:deploy.sh 自身
  的改動,該次部署以「舊版腳本」執行「新版程式碼」,新版腳本下次生效。
- **smoke = 全套 pytest**(~19s,217 tests)。不挑子集——便宜且覆蓋完整。
  `uv sync --frozen` 預設含 dev group,pytest 可用。
- **crontab 全量覆蓋**:`crontab scripts/cn02.crontab` 直接取代 reidlin 的
  per-user crontab。**遷移前置(第一次部署前,手動)**:
  `crontab -l` 快照 cn02 現況寫入 repo 的 `scripts/cn02.crontab`,之後
  全量覆蓋才安全(reidlin 的所有 cron 條目自此由 repo 納管)。檔頭註明
  「此檔為 reidlin@cn02 crontab 唯一真相」。
- 舊 `scripts/twstock-screener.cron`(/etc/cron.d 格式、user `reid` 本地
  佈局)標註 deprecated,指向新檔。

## 4. `scripts/notify_deploy.py`(~20 行)

重用 `twstock_screener.notify.send_alert`:

- `pattern="deploy"`、`transition="deploy_failed"`、`stock_id=<短SHA>`、
  `run_date=today`、message 為 CLI 第一參數。
- 冪等鍵 `(run_date, stock_id, pattern, transition)` 含 SHA → **同一 commit
  失敗只通知一次**(重跑不洗版),不同 commit 各自通知。
- chat_id / bot_token 從既有 `Settings`(.env)取——與每日 digest 同一組態,
  零新 secret。GitHub 端**不需要任何 secret**(部署在 cn02 本地執行)。

## 5. `.github/workflows/deploy.yml`(薄殼)

```yaml
name: deploy-cn02
on:
  push:
    branches: [master]
concurrency:
  group: deploy-cn02
  cancel-in-progress: false   # 排隊,不腰斬進行中的部署
jobs:
  deploy:
    runs-on: [self-hosted, cn02]
    timeout-minutes: 15
    steps:
      - name: Run deploy script on production checkout
        run: bash "$HOME/stock/scripts/deploy.sh"
```

- **不用 `actions/checkout`**:生產目錄自己 `git pull`;runner 的 `_work`
  目錄不參與部署(避免兩份 checkout 混淆、避免在 _work 裡跑出第二個環境)。

## 6. 測試與驗證

- 本地可測:`bash -n` + shellcheck 過 `deploy.sh`;`notify_deploy.py` 單元
  測試(mock `send_alert`,驗 pattern/transition/stock_id 參數)。
- 端到端(僅能在 cn02 驗):
  1. 首次部署後:GitHub UI run 日誌全綠 + cn02 `git log -1` 為 merge SHA。
  2. 失敗路徑演練一次(不污染 master):在 cn02 手動執行
     `bash scripts/deploy.sh` 並預先製造一個必敗條件(例如暫時 `chmod -x`
     不影響資料的路徑或以環境變數注入必敗 smoke),確認 Telegram 收到
     `DEPLOY FAILED @ <sha> — step: ...`。驗完復原、確認下次手動執行全綠。

## 7. 回退方案(若 runner 端點不可達)

cn02 網路若擋 GitHub Actions broker(前置驗證失敗),放棄 self-hosted
runner,改 pull 型:cn02 crontab 每 10 分鐘 `git fetch` + 版本比對觸發同一個
`deploy.sh`(除 pull 觸發源外,其餘設計原封不動)。deploy.sh 與 crontab 檔
的設計對兩種觸發源皆有效——這是把部署邏輯放 `deploy.sh` 而非 workflow 的
原因之一。

## 8. 不做(YAGNI)

- 不做回滾機制(`git revert` + merge 即回滾,走同一條部署路)。
- 不做部署鎖定視窗(cron job 皆短任務;git pull 原子性足夠)。
- 不做 hosted CI 測試 job(日後可加,與本 spec 無關)。
- 不動 DB/`data/`/`.env`/secrets。
- 不做多環境(只有 cn02 一台生產機)。
