# GitHub Actions 部署 s5xq-cn02(merge → master 觸發)

**日期**: 2026-07-02
**範圍**: `.github/workflows/deploy.yml` + `scripts/deploy.sh` + `scripts/cn02.crontab` + `scripts/notify_deploy.py`;另含 cn02 一次性佈建(專用 runner 使用者 + sudoers + runner service,手動步驟文件化在 plan)

## 背景與動機

生產 pipeline 跑在 s5xq-cn02(user `reidlin`,per-user crontab,uv 在
`~/.local/bin`,專案在 `~/stock`)。目前 merge 到 master 後要手動上 cn02
`git pull`,容易忘、無驗證、無通知。目標:merge → master 自動部署,失敗走既有
Telegram 通道通知。

## 關鍵約束(已確認)

- **cn02 只能 outbound**:無入站 SSH(連 Telegram 都要走 `notify.py` 的
  DoH-pinned relay)→ push 型(hosted runner SSH 進來)不可行。用
  **self-hosted runner**(outbound long-poll)。
- **Repo 是 PUBLIC**:self-hosted runner 在 public repo 是已知風險面
  (GitHub 官方不建議)。使用者**已決定保留 runner + 盡最大實務硬化並誠實
  記錄殘餘風險**(非改架構;polling 回退見 §7)。
- **部署範圍**:git pull + uv sync + smoke + crontab 同步。不碰
  DB/`data/`/`.env`。
- **失敗通知**:走既有 DoH relay Telegram(但通知器本身必須與專案環境**脫鉤**,
  見 §4)。成功靜默(Actions 綠勾即回執)。

## 威脅模型(先講清楚,硬化才有的放矢)

部署 master **本質上就是在 cn02 執行 merged 程式碼**——smoke 的 `pytest`
會跑 repo 測試碼,而「跑新版程式」正是部署目的。所以對 master 路徑,runner
與 polling 在「merged 碼在 cn02 執行」這件事上等價。**runner 相對 polling 的
增量風險**只有兩項:(a) **非 master 分支上的 workflow** 也能指向 runner;
(b) runner 常駐、持有 GITHUB_TOKEN 與檔案系統存取。硬化因此聚焦於
(a) 限制哪個 workflow/ref 能用 runner、(b) 最小化 token/持久性與**爆炸半徑**,
而非試圖阻止「merged 碼執行」(那就是部署本身)。

## 1. 觸發與安全硬化

- 觸發只有 `push: branches: [master]`。deploy.yml 是**唯一**允許落在
  self-hosted 的 workflow;未來任何 `pull_request`/CI 一律
  `runs-on: ubuntu-latest`。
- **OS 使用者隔離(最重要的爆炸半徑控制)**:runner 以**專用低權限使用者**
  `ghrunner` 執行(非 `reidlin`,不擁有任何機密)。workflow 唯一動作是經
  sudoers 呼叫 scoped 進入點:
  ```
  # /etc/sudoers.d/ghrunner-deploy(root 擁有)
  ghrunner ALL=(reidlin) NOPASSWD: /home/reidlin/stock/scripts/deploy.sh
  ```
  **關鍵不變式**:`deploy.sh` 由 `reidlin` 擁有、`ghrunner` **不可寫**(位於
  `~reidlin/stock`,與 runner 的 `_work` checkout 分離);sudoers 只准這**一條
  無參數**命令。→ 落在 runner 的 rogue workflow(以 `ghrunner` 身分)讀不到
  reidlin 的 `.env`/DB/rclone,也只能觸發一次「部署 master」(deploy.sh 自己
  pull master,不跑攻擊者分支碼)。
- `permissions: {}`(deploy.yml 不需要 token)。deploy 工作流**不使用任何
  第三方 action**(無 `uses:`),消除 action 供應鏈面。
- **分支保護 ruleset(全分支)** + CODEOWNERS(= 擁有者)保護
  `.github/workflows/**`、`scripts/deploy.sh`、`scripts/cn02.crontab`:改這些
  檔要 PR + review;並限制分支建立,防止有人直接 push 一個帶 `on: push` +
  `runs-on:[self-hosted]` 的新 workflow 分支。
- **runner-group workflow 限制**(把 runner 綁死只給 `deploy.yml`)是最強
  控制,但 **runner groups 是 org 功能**;個人 repo 沒有。若要這層,建議把
  repo 移到 **one-person org**(仍可 public)——列為**選配加強**,非本 spec
  必需。
- `--ephemeral`/JIT runner:**歸類為衛生、非隔離**。它不清主機檔案系統、也不
  阻止 job 在機器上留痕,且 outbound-only 主機做 JIT 還得存一份註冊用 PAT
  (新機密要保護)。→ 用**常駐 service + 使用者隔離**為主,ephemeral 選配。
- 「outside collaborator approval」視為次要——真正風險是**同 repo write 權限
  者的分支**,不是 fork。

**誠實殘餘風險(寫進 spec,不粉飾)**:具 write 權限的帳號被盜或協作者惡意,
仍能把碼 merge 進 master → 在 cn02 以 `reidlin` 執行(與 polling 對 master
相同)。使用者隔離把**非 master / rogue-workflow** 面壓到「只能以無機密的
`ghrunner` 跑碼或觸發一次 master 部署」。這是 public repo + self-hosted 的
固有殘餘,使用者已接受。

## 2. cn02 一次性佈建(手動,文件化在 plan)

- 建立 `ghrunner` 使用者;裝 GitHub Actions runner 於
  `~ghrunner/actions-runner`;systemd service(可 system-level 指定
  `User=ghrunner`,或 ghrunner 的 user service + linger)。
- 佈 `/etc/sudoers.d/ghrunner-deploy`(見 §1)。
- **前置驗證**:先手動 `./run.sh` 確認 runner 能連 GitHub broker 端點
  (cn02 網路特殊;git 可用 ≠ runner 端點可用)。**不通則此架構止步**,回退
  §7 polling。
- 確認 `deploy.sh`/`cn02.crontab`/`.env` 的擁有權與權限(reidlin 擁有,
  ghrunner 不可寫)。

## 3. `scripts/deploy.sh`(部署邏輯唯一真相;runner 與 polling 共用)

```bash
#!/usr/bin/env bash
set -Eeuo pipefail        # -E: ERR trap 需傳入 function,否則 main() 內失敗不觸發

PROJECT="$HOME/stock"
UV="$HOME/.local/bin/uv"
LOCK="$PROJECT/.deploy.lock"
CURRENT_STEP="init"

notify_failure() {
  local sha
  sha=$(git -C "$PROJECT" rev-parse --short HEAD 2>/dev/null || echo unknown)
  # 獨立 stdlib 通知器,走 SYSTEM python3,零專案 import → venv 壞掉也能通知
  python3 "$PROJECT/scripts/notify_deploy.py" \
    --env-file "$PROJECT/.env" \
    --sha "$sha" \
    --message "🚨 DEPLOY FAILED on cn02 @ ${sha} — step: ${CURRENT_STEP}" \
    || echo "DEPLOY-NOTIFY-FAILED (see spool ~/.deploy-notify/) — original step: ${CURRENT_STEP}" >&2
}
trap notify_failure ERR

main() {
  cd "$PROJECT"
  # flock 從 git pull 前就上鎖,涵蓋整段;所有 cron 進入點共用此鎖(§5)
  exec 9>"$LOCK"
  flock -w 300 9 || { CURRENT_STEP="lock"; return 1; }

  CURRENT_STEP="git pull";  git pull --ff-only origin master
  CURRENT_STEP="syntax";    bash -n "$PROJECT/scripts/deploy.sh"   # 抓「新版腳本壞掉→下次跑爆」
  CURRENT_STEP="uv sync";   "$UV" sync --frozen --extra dev
  CURRENT_STEP="smoke";     TWSTOCK_DB_PATH=:memory: "$UV" run pytest -m "not slow" -q
  CURRENT_STEP="crontab";   install_crontab
}

install_crontab() {
  local f="$PROJECT/scripts/cn02.crontab"
  test -s "$f"                                  # 拒絕空檔(避免靜默清空排程)
  grep -q "^# MANAGED-BY: repo scripts/cn02.crontab" "$f"   # sentinel:證明是受管檔
  crontab -l > "$HOME/.crontab.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  crontab "$f"
}

main "$@"
```

設計要點:

- **自更新防護**:全腳本包 `main(){...}; main "$@"`,bash 先 parse 完整檔才執行,
  `git pull` 中途替換本體不會執行錯亂。**已知特性**:deploy.sh 自身的改動,該次
  以「舊版腳本」執行「新版程式碼」,新版腳本下次生效。`bash -n` 步驟先攔住
  「新版腳本語法壞掉」以免下次整個爆掉。
- **ERR trap 真的會觸發**:`set -Eeuo pipefail` 的 `-E`(errtrace)讓 `trap ERR`
  傳入 `main()`/`install_crontab`。
- **smoke**:`uv sync --frozen --extra dev`(pytest 在 `[optional-dependencies]
  .dev`,不加 `--extra dev` 不會裝)+ `pytest -m "not slow"`(排除吃真
  `data/twstock.db` 的 `@slow` benchmark)+ 明設 `TWSTOCK_DB_PATH=:memory:`
  (即使 cn02 shell 已 export 真 DB 路徑也維持確定性)。
- **crontab 全量覆蓋 + 護欄**:`test -s` 拒空檔、sentinel header 證明受管、
  覆蓋前先 timestamped 備份 `crontab -l`。**遷移前置**(第一次部署前,手動):
  `crontab -l` 快照 cn02 現況 → 寫進 `scripts/cn02.crontab`,之後覆蓋才安全。
- **flock 從 pull 前上鎖**:涵蓋 pull→sync→smoke→crontab 整段,避免 cron job
  在 `uv sync` 改 `.venv` 或 `git pull` 換檔時啟動。

## 4. `scripts/notify_deploy.py`(獨立、stdlib-only、與專案環境脫鉤)

- **零專案 import、只用標準庫**:`urllib.request` 發 Telegram POST,沿用
  `notify.py` 的 DoH-pinned-IP 手法(但 stdlib,非 httpx)。→ 以 SYSTEM
  `python3` 執行,`uv sync` 弄壞專案 venv 也照樣能通知。
- **自己讀設定**:`--env-file $PROJECT/.env` 解析 `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_CHAT_ID`(專案是從 `.env` 經 `Settings` 取,不保證在 shell env)。
- **去重用 marker 檔**:`~/.deploy-notify/<date>-<sha>`(與 `notify.py` 的
  `date+sha` 語意一致,非純 sha——避免同一 commit 日後合法重試被永久抑制)。
  → 部署**不寫 `notification_log`**,故 deploy 真正 DB-write-free。
- **通知失敗不遮蔽部署錯誤**:POST 失敗 → 印 loud stderr + append 本地 spool
  檔(`~/.deploy-notify/spool.log`);deploy.sh 的 ERR trap 已保證 job 以非零
  退出、Actions 顯紅。通知器只負責讓「通知本身也失敗」這件事可見。

## 5. `scripts/cn02.crontab`(受管檔,每條持鎖)

- 檔頭 sentinel:`# MANAGED-BY: repo scripts/cn02.crontab`(deploy.sh 檢查它)。
- reidlin per-user 格式、cn02 實際路徑。**每條命令包 flock 共用同一把鎖**,
  才不會在部署中途啟動:
  ```
  # MANAGED-BY: repo scripts/cn02.crontab
  UV=/home/reidlin/.local/bin/uv
  LOCK=/home/reidlin/stock/.deploy.lock
  20 8 * * 1-5  flock -w 60 $LOCK -c 'cd ~/stock && $UV run python scripts/analyze.py >> logs/analyze.log 2>&1'
  # ...(fetch / backfill / metadata / drive backup 同法包 flock)
  ```
- **首次 rollout 特例**:現行 prod crontab 各條**還沒**持鎖 → 第一次部署仍需
  一個靜默窗(或一次性手動先把持鎖版 crontab 裝上)。plan 明列此步。
- **鎖只在所有進入點都遵守時才完全消除競態**:cron 條目會;**臨時手動**執行
  repo 碼者不會,除非也包 flock——plan 文件化此注意事項。
- 舊 `scripts/twstock-screener.cron`(/etc/cron.d 格式、user `reid` 本地佈局)
  標註 deprecated,指向本檔。

## 6. `.github/workflows/deploy.yml`(薄殼)

```yaml
name: deploy-cn02
on:
  push:
    branches: [master]
permissions: {}              # 這個 workflow 不需要 GITHUB_TOKEN
concurrency:
  group: deploy-cn02
  cancel-in-progress: false  # 連續 merge 排隊,不腰斬進行中的部署
jobs:
  deploy:
    runs-on: [self-hosted, cn02]
    timeout-minutes: 15
    steps:
      - name: Run scoped deploy as reidlin
        run: sudo -H -u reidlin /home/reidlin/stock/scripts/deploy.sh
        # -H 設 $HOME=/home/reidlin,否則 deploy.sh 的 $HOME/stock 會指錯
```

- **不用 `actions/checkout`、不用任何第三方 action**:runner 以 `ghrunner`
  身分,唯一動作是 sudo 呼叫 reidlin 擁有的 `deploy.sh`;生產目錄自己 pull。
- 第二個 merge 在第一個失敗後**仍應執行**(可能含修復),`cancel-in-progress:
  false` 排隊即可。concurrency 只管 Actions;手動/§7 polling 靠 §5 的 flock。

## 7. 回退方案(若 runner 端點不可達)

前置驗證失敗則放棄 runner,改 polling:cn02 crontab 每 10 分鐘
`flock … git fetch` + 版本比對觸發**同一個 `deploy.sh`**(除觸發源外設計原封
不動——把部署邏輯放 deploy.sh 而非 workflow,正是為了對兩種觸發源皆有效)。

## 8. 測試與驗證

- 本地可測:`bash -n` + shellcheck 過 `deploy.sh`;`notify_deploy.py` 單元測試
  (mock/攔截 `urllib` 送出,驗 DoH 解析、.env 解析、marker 去重、失敗 spool)。
- 端到端(僅 cn02 可驗):
  1. 首次部署後:Actions run 全綠 + cn02 `git log -1` 為 merge SHA +
     `sudo -u reidlin` 確實生效(檔案由 reidlin 更新)。
  2. 失敗路徑演練(不污染 master):cn02 上手動製造必敗條件執行
     `sudo -u reidlin scripts/deploy.sh`(如注入必敗 smoke),確認 Telegram 收到
     `DEPLOY FAILED @ <sha> — step: smoke`;再驗「通知器本身失敗」時 stderr +
     spool 有痕跡。驗完復原。
  3. 權限驗證:確認 `ghrunner` 無法讀 `~reidlin/.env`、無法寫 `deploy.sh`、
     sudoers 只准那一條命令(試 `sudo -u reidlin bash -c ...` 應被拒)。

## 9. 不做(YAGNI)

- 不做回滾機制(`git revert` + merge 即回滾,走同一條路)。
- 不做 hosted CI 測試 job(日後可加 `ci.yml`,`ubuntu-latest`,與本 spec 無關)。
- 不動 DB/`data/`/`.env`/secrets 內容;部署只動 code + deps + crontab。
- 不做多環境(只有 cn02 一台生產機)。
- 不強制 ephemeral/JIT runner(衛生非隔離,且需存 PAT;使用者隔離已是主要控制)。
- 不遷 one-person org(列為選配加強,非必需)。
