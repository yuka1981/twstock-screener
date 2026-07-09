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
