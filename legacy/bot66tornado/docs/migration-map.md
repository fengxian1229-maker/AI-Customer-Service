# Migration Map

這份表只記錄已從舊版確認來源的內容。找不到對應來源的，不填文案。

## 已搬入

| 新版位置 | 舊版來源 | 狀態 |
|---|---|---|
| `src/content/menus.js` | `workspace-autoreply-clean/lc-rich.js` main / withdrawal / previous menus | 已搬入，所有客戶按鈕保留 emoji |
| `src/content/templates.js` | `workspace-autoreply-clean/lc-rich.js` `FLOW_MESSAGES` 部分鍵 | 已搬入 |
| `src/content/templates.js` handoff | `workspace-autoreply-clean/case-replies.js` `composeEscalateMessage` | 已搬入 |
| `src/core/waiting-backend-classifier.js` | 新規格：硬訊號分類 | 已實作，沒有用情緒判定 |
| `docs/客服bot完整步驟圖.pdf` | `workspace-autoreply-clean/docs/客服bot完整步驟圖.pdf` | 已複製 |
| `src/config/platforms.js` | `workspace-autoreply-clean/livechat-poller.js` `PLATFORM_TOPICS` / `PLATFORM_MERCHANTS` + `platform-switches.*.json` | 已搬入，正式群與測試群分離 |
| `platform-switches.official.json` | `workspace-autoreply-clean/platform-switches.official-all.json` | 已搬入 |
| `platform-switches.test.json` | `workspace-autoreply-clean/platform-switches.test.json` | 已搬入 |
| `src/adapters/livechat-rich.js` | `workspace-autoreply-clean/lc-rich.js` `buildQuickRepliesEvent` | 已搬入 payload 結構 |
| `src/runtime/engine.js` TG reply policy | `workspace-autoreply-clean/livechat-poller.js` `pollTelegram` 只處理 reply-to mapping | 已重做成乾淨閘門：非主卡回覆不轉客戶 |

## 仍缺舊版對應文案，暫不自行補

| 情境 | 目前處理 |
|---|---|
| SOP 後 `已解決` / `還是不會` 專用按鈕文字 | 舊版未找到專用按鈕；目前用舊版既有主選單/提款選單接續，不新增按鈕文字 |
| 後台回覆後 `補充同一案件 / 新問題 / 真人客服` 三按鈕 | 舊版未找到完整對應，先不填 |
| 「找不到問題」類按鈕 | 舊版只有 `Atención humana`，先只使用真人客服 |
