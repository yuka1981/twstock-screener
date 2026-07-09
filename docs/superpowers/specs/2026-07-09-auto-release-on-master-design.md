# 設計：合併到 master 自動建立 release tag 與 notes

- 日期：2026-07-09
- 狀態：已核准，待實作
- 相關：`.github/workflows/deploy.yml`（既有部署流程，本設計與之獨立）

## 目標

master 每落地一個 commit（PR merge 或直接 push），自動：

1. 打一個**日期式 CalVer** tag（`v<台北日期>`，同日多次加 `-N` 尾碼）。
2. 建立對應的 GitHub Release，內容用 **GitHub 原生 auto-generated notes**。

不需維護版號檔、不寫 CHANGELOG、不改 `pyproject.toml`（版號留 `0.1.0`）。Release notes 就活在 GitHub Releases。

## 已決定的取捨（brainstorm 定案）

| 項目 | 決定 | 理由 |
|---|---|---|
| 版號策略 | 日期 CalVer，`v2026.07.09`，同日加 `-2`/`-3` | 零版號來源；每次部署可追溯；最貼合「合併即發」 |
| Notes 生成 | GitHub 原生 `--generate-notes` | 零維護；commit 已守 Conventional Commits，PR 帶 `#NN`，原生列表已夠用 |
| 觸發語意 | **凡落地 master 就發**（含直接 push） | 維護者有時直接 push（如 `80ad9a6`），那些也該有 release；PR-merge-only 會漏掉 |
| 與 deploy 關係 | 獨立、平行、不綁定 | 有 Release ≠ 已成功部署；耦合跨 workflow 機件不划算，且 revert→新 release 自我修正 |

## 設計

新增獨立 workflow `.github/workflows/release.yml`，與 `deploy.yml` 分離。

### 為何獨立一個檔（而非併進 deploy.yml）

- `deploy.yml` 刻意綁在 **self-hosted 生產機 cn02**、`permissions: {}`。建 tag/release 需要 `contents: write` token，**不該進生產機**。
- 分檔跑在 **GitHub-hosted `ubuntu-latest`**，把 write token 隔離在 prod 外。
- `.github/CODEOWNERS` 已要求 `@yuka1981` 審所有 workflow 變更，分檔不削弱審查。

### Workflow 內容

```yaml
name: release
on:
  push: { branches: [master] }          # 僅 master；無 workflow_dispatch（見「驗證計畫」）
concurrency: { group: release, queue: max }   # FIFO 排隊、不丟棄 pending run
permissions: { contents: write }
jobs:
  release:
    runs-on: ubuntu-latest                      # 刻意用 hosted，write token 不碰 cn02
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0, fetch-tags: true }   # fetch-depth:0 已含全部 tag，供 compute_tag 判存在性
      - env: { GH_TOKEN: "${{ github.token }}" }
        shell: bash                                # 明示 bash（GH Linux 預設即 bash -eo pipefail）
        run: |
          set -euo pipefail
          source scripts/release_tag.sh           # 純函式 compute_tag / select_prev（可離線自測）

          # 先自我把關：邏輯回歸就在動到 release 之前紅燈
          bash scripts/tests/test_release_tag.sh

          today="$(TZ=Asia/Taipei date +%Y.%m.%d)"   # 台北日期，非 runner 的 UTC
          existing="$(git tag --list 'v*')"          # 簡單賦值：git 失敗 → set -e 中止
          TAG="$(printf '%s\n' "$existing" | compute_tag "$today")"

          # capture-first fail-closed：gh 的 auth/API 失敗發生在「簡單賦值」上，
          # set -e 直接中止整個 run（不依賴 pipefail+substitution 的微妙交互；bash/zsh 皆驗證會中止）。
          # 只有「gh 成功但無 CalVer release」（首發）時 released 為空 → NOTES_START 為空。
          released="$(gh release list --limit 100 --json tagName,isDraft \
                        --jq '.[] | select(.isDraft==false) | .tagName')"
          NOTES_START="$(printf '%s\n' "$released" | select_prev)"

          args=(--target "$GITHUB_SHA" --title "$TAG" --generate-notes --fail-on-no-commits)
          [ -n "$NOTES_START" ] && args+=(--notes-start-tag "$NOTES_START")
          gh release create "$TAG" "${args[@]}"
```

### `scripts/release_tag.sh`（純函式，無 git/gh/網路副作用）

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
# 收緊的 regex 只認 v<YYYY.MM.DD> 與其 -N 尾碼。
# 用 sed -n：無匹配自然 0 退出（不需 || true），故 sort/tail 的真實失敗會照常傳播 → fail-closed。
select_prev() {
  sed -nE '/^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}(-[0-9]+)?$/p' | sort -V | tail -n1
}
```

### 關鍵設計點與理由

- **`concurrency: {group: release, queue: max}`**：`queue: max` 讓 run FIFO 排隊（最多 100 pending）、**不取消 pending run**（預設 `single` 只有 1 個 pending 名額，連續 merge 會掉 release）。序列化後**同日 tag 建立競態消失**；notes 重疊競態則**在 commit 順序成立時消失**——殘留的亞秒級 queue reorder 見〈已知限制 1〉，此處不宣稱更強保證。（`queue: max` 與 `cancel-in-progress: true` 併用為 validation error，故不設 `cancel-in-progress`。）
- **`TZ=Asia/Taipei`**：runner 是 UTC，不校正時區的話台灣晚間的 merge 會標成前一天。
- **同日尾碼（`compute_tag`）**：以 `git tag --list 'v*'` 的快照精確整行比對跳號（比對 **tag 存在性**，不是 release 存在性）——若前次部分失敗留下 orphan `vD` tag，會跳到 `vD-2`，避免把新 commit 的 release 掛到指向舊 commit 的 stale tag。
- **`--notes-start-tag` 取自「已發布 release」（`select_prev`）**：`sort -V | tail -1` 明確排序，不依賴 `gh release list` 未文件化的排序鍵，也不受 orphan git tag 汙染。
- **`prev` 選擇 fail-closed（capture-first）**：先把 `gh release list` 輸出抓進**簡單賦值** `released=$(...)`，再 pipe 給 `select_prev`。gh 的 auth/API 失敗發生在簡單賦值上 → `set -e` 中止整個 run（**不會**用錯 base 照發），且不依賴「pipefail 穿過 pipeline 進 substitution」那個跨 shell 不一致的微妙交互（已於 bash 與 zsh 各驗一次：capture-first 皆中止）。`select_prev` 用 `sed -n`，**無匹配自然 0 退出、不需 `|| true`**，所以「首發無 CalVer」被容忍、而 `sort`/`tail` 的真實失敗仍會傳播中止（實測：注入失敗的 `sort` 會讓 `select_prev` 非零）。step 明示 `shell: bash`。
- **`--fail-on-no-commits`**：原生冪等守衛。對「已發布 commit」的重跑，因「距上個 release 無新 commit」而紅燈擋下，不產生重複 release；首發豁免。
- **失敗處理**：任何不可恢復錯誤 → step 非零退出 → run 紅燈 → 靠 GitHub 原生失敗通知（Actions UI + 寄給 actor）。**不**在 hosted runner 重建 `deploy.yml` 的 Telegram 通道（那需要把 bot secret 佈進本 repo 的 Actions，僅為 release 失敗不划算）。
- **無 `workflow_dispatch`**：不提供手動觸發。手動 dispatch 在 master 上會產生**真實** release（不是 dry-run），在 feature branch 上又可能鑄錯 tag；兩者皆不值得。純邏輯以離線自測驗證，live 路徑由第一次真實 merge 驗證（見〈驗證計畫〉）。失敗後的重跑用 Actions UI 原生 re-run，不需 dispatch。

## 已知限制（刻意不處理，可日後升級）

1. **Queue 順序 ≠ commit 順序**：`queue: max` 是「進入等待的先後」FIFO，非 commit 祖先序。理論上若後代 commit 的 run 先跑，同日尾碼序數會對調、notes 範圍略重疊。順序人工合併下 reorder 視窗為亞秒級、實務不可達，且後果純 cosmetic（無資料遺失、無指錯 commit）。升級路徑：以 `$GITHUB_SHA` 的 commit 祖先關係挑 `prev`。
2. **Orphan tag**：`gh release create` 是單一原子 API call，「tag 建好但 release 沒建」近乎不發生；真發生時該 commit 本來就沒 release，補一個 `-N` release 是它唯一正確的 release，另留一個無害 orphan tag。升級路徑：偵測「tag 存在但無對應 release」時對既有 tag 補建 release。
3. **`queue: max` 上限 100 pending**：超過會取消。此 repo 流量不可能觸及。
4. **暫時失敗 ＋ 在後續 release 之後重跑 → 該 commit 可能變不可發布**：若某 commit A 的 release run 暫時性失敗（gh／網路抽風），且在重跑前已有更晚的 commit B 先 ship 成 release，則重跑 A 時 `select_prev` 會挑到 B（A 的後代）當 notes base，`--fail-on-no-commits` 因「距上個 release（B）無新 commit」而**拒絕** → A 拿不到自己的 release。此案**非無聲**（重跑會紅燈、維護者看得到）、**低機率**（需「暫時失敗＋B 先 ship＋手動重跑」複合事件，單人順序合併下罕見）、**低影響**（A 的變更仍在 master、仍由獨立的 `deploy.yml` 部署，且 A 的 commit 會落進下一個 release 的 notes 範圍；丟的只是 A 專屬的 release 物件）。手動補救：對 A 手動 `gh release create`，或接受它併入下一個 release 的 notes。升級路徑（擇一）：(a) 動 tag 前查「`$GITHUB_SHA` 是否已有 release」做 per-SHA 冪等短路，並**拿掉 `--fail-on-no-commits`**——重跑失敗的 A 會補出 release（日期為重跑日）、成功重跑變 no-op、不需 ancestry，代價是 A 的日期偏移＋可能 notes 重疊（cosmetic）；(b) 改用 ancestry-aware `select_prev`（以 `$GITHUB_SHA` 的祖先關係挑最近的已發布 tag），最穩健但最複雜。此限制由 2026-07-09 對抗式 review（Codex）指出，經評估對本 repo 風險比後刻意延後。

## 驗證計畫

可執行契約，不是「之後再說」：

- **離線單元自檢（必備、簽入）**：`scripts/tests/test_release_tag.sh`，`source scripts/release_tag.sh` 後對純函式 assert。
  - **確切執行方式**：`bash scripts/tests/test_release_tag.sh`（退出碼 0 = 全過）。
  - **必含案例**（餵 fixture、不碰 git/gh）：
    1. `compute_tag`：空 tag 集 → `v2026.07.09`；含 `v2026.07.09` → `-2`；含 `v2026.07.09` 與 `-2` → `-3`。
    2. `select_prev`：混入 `phase-p1` 與雜訊 `v2026.07.09foo` 仍回最新 CalVer（如 `v2026.07.10`）。
    3. `select_prev`：`v2026.07.09-10` 版本序大於 `-2`（`sort -V` 數值感知）。
    4. `select_prev`：無任何 CalVer → 空字串。
  - **同時在 release job 內先跑一次**（見 YAML）：邏輯回歸會在動到 release 之前紅燈，等於每次 run 都重驗契約。
  - **設計期已實跑證據**：上述 4 組 assert（實作為 8 條）在 scratchpad 以 `bash` 執行全過；capture-first fail-closed（模擬 gh 失敗）在 **bash 與 zsh** 皆正確中止；`select_prev` 注入失敗的 `sort` 會傳播非零（不被吞）；首發空輸入得空字串、exit 0。實作時把同一組搬進 repo 即可。
- **端到端（無需 dry-run，因無 junk）**：本 workflow **沒有** dry-run 模式；手動 dispatch 已移除。live 驗證＝**加入 `release.yml` 的那個 PR 合併進 master** 時，該 push 觸發 workflow、對這個合法變更產生第一個 release。（機制：GitHub 對 push 事件是用**該事件關聯 commit（即這個 merge commit）裡的** workflow 檔——而 merge commit 已含 `release.yml`——所以它會在自己的 merge 上執行、自我 bootstrap；並非「push 一律讀預設分支的 workflow 檔」。）檢查該 Release 的 tag 名與 notes 是否正確；若首個 run 失敗，只是紅燈、`deploy.yml` 不受影響，修正後重新 merge 即可，**不會**留下 junk release。
  - 首個 CalVer release 因無前一個 CalVer release，`NOTES_START` 為空、原生 auto-base 可能回退到 `phase-p1`，notes 較長，一次性、可接受。

## 影響檔案

- 新增：`.github/workflows/release.yml`（上方 YAML）
- 新增：`scripts/release_tag.sh`（純函式 `compute_tag` / `select_prev`，上方內容）
- 新增：`scripts/tests/test_release_tag.sh`（上述 4 組 assert，`bash` 可獨立跑）
- 不動：`deploy.yml`、`pyproject.toml`、無 CHANGELOG
