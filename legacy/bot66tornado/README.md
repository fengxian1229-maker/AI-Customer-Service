# bot66tornado (Legacy Prototype)

> This Node.js application is retained for historical reference and emergency
> comparison. The production service is the Python application in
> `../../src/app/`; this directory is not a second production entry point.
>
> The original `src/runtime/` implementation was local-only and ignored by
> Git. Restore that directory from the operator backup before running this
> prototype; commands requiring it will fail fast when it is absent.

乾淨版客服 Bot。這裡不是舊專案複製品，而是把舊版可用的文案/SOP 搬進新的窄路徑架構。

## 工作版定位

本目录现在位于主仓库的 `legacy/bot66tornado/`，仅作为历史原型保留。

- 不自動同步 Desktop 版本。
- 不自動部署到正式 LiveChat。
- 每次改動都要記錄在 `docs/修改紀錄.md`，使用 `BT66-XXX` 編號。
- 修改紀錄只要求 BT66 編號。

## 目前原則

- 客戶沒有按按鈕前，不猜業務流程，只引導按按鈕或做當下該做的事。
- 每個 Bot 回合都要有明確下一步：按鈕、固定資料、SOP、等待後台、轉真人或 soft parked。
- 存款未入帳與提款未收到必須收齊用戶名/註冊電話 + 對應截圖，才送 TG。
- TG 後台只透過回覆主卡/字卡給客戶；一般 topic 訊息不送客戶。
- 等後台時不判斷「客戶多生氣」，只按硬訊號分：補資料、要真人、其他追問。
- 所有客戶選單按鈕都保留 emoji 作為快速辨識；客戶文字訊息仍維持禮貌、簡潔。
- 舊版找不到對應文案/SOP 的步驟，先標記 missing，不自行創作。

## 來源

- 舊版文案來源：`../workspace-autoreply-clean/lc-rich.js`
- 舊版 handoff 文案來源：`../workspace-autoreply-clean/case-replies.js`
- 舊版圖片與後台查詢來源：`../workspace-autoreply-clean/livechat-poller.js`、`../workspace-autoreply-clean/direct-query.js`
- SOP 來源：`../workspace-autoreply-clean/docs/SOP-*.md`
- 手冊規則來源：`../workspace-autoreply-clean/reports/manual-rules-extract/客服手冊規則抽取與路窄版衝突表.md`
- 產品流程圖：`docs/客服bot完整步驟圖.pdf`

## 結構

```text
src/content/    從舊版搬來的按鈕、文案、SOP
src/core/       純邏輯：狀態機、訊號分類、owner、guards
src/flows/      預留的流程拆分位置；目前主要路徑仍集中在 src/core/state-machine.js
src/adapters/   LiveChat / Telegram / 後台查詢 adapter
src/runtime/    case store
tests/          路徑與規則測試
```

## 目前可用指令

```bash
npm test
npm run smoke
npm run route:gate
npm run batch:path-review
npm run replay:real
npm run replay:human-seeds
npm run live:sim
npm run offline:real
npm run review:test-live
npm run export:latest-test-chat
npm run start:test
npm run start:test:live
npm run preflight:test:live
npm run preflight:official
npm run go:no-go:official
npm run postlaunch:official
npm run doctor:processes
npm run status:test:live
npm run stop:test:live
npm run watch:test:live
npm run status:test
npm run stop:test
npm run start:official
npm run watch:official
npm run status:official
npm run health:official
npm run launchd:install:official
npm run launchd:status:official
npm run launchd:uninstall:official
npm run stop:official
```

- `npm test`：跑核心規則測試，不連真 LC/TG。
- `npm run smoke`：跑幾條完整 dry-run 路徑，輸出會送給 LC/TG 的動作，不連真 LC/TG。
- `npm run route:gate`：把主要窄路徑走到底，不連真 LC/TG，確認沒有停在半路。
- `npm run batch:path-review`：批量跑 7 個正式平台 + 測試群的窄路徑完整模擬；它會真的經過 `BotEngine -> CommandRunner -> 假 LC/TG/後台 adapter`，並輸出中文報告到 `reports/batch-path-review/latest.md`。
- `npm run replay:real`：用正式群真實聊天序列回放，現在會檢查資料齊全未送 TG、明確自由文字卡主選單、主選單洗版、已知真人案例錯查流水/錯送 TG。
- `npm run replay:human-seeds`：用 8000+ 真人客服資料抽樣靈感做第一句真實問題壓力測試。
- `npm run live:sim`：真通道模擬客戶；預設只打 LiveChat group 23，會真的經過 LiveChat、test-live bot、direct-query 後台、Telegram 測試群與 LiveChat 回覆。假客戶會用真人風格語料池抽不同說法，並預設跑到客戶旅程真的結束。存提款 TG 案件必須等真 TG 後台回覆後，再由假客戶回覆「已解決」，bot 成功收尾才算結束；若只要快速檢查路由與送 TG，可加 `-- --allow-pending-tg-end`。若 official bot 已在跑，工具不會另開 test-live 來搶 Telegram；可改用 `-- --mode=official --group-id=23 --confirm-live-official=YES` 讓既有 official bot 處理 group 23。
- `npm run offline:real`：離線 LiveChat 客戶模擬；不建立真 LiveChat 聊天，而是在本機直接跑 `BotEngine -> CommandRunner` 並保存 transcript 到 `reports/offline-real-channel-sim/latest.md`。TG 案件可用 `-- --confirm-real-tg=YES` 真的送到 `TELEGRAM_TEST_GROUP`，流水查詢可用 `-- --confirm-real-backend=YES` 真的走 `direct-query.js` 後台；本機驗證可先加 `-- --dry-run-tg --dry-run-backend`。`human-seeds` 模式會從 8266 筆真人聊天抽語氣與分類，但電話/email/長 ID 會遮罩，截圖固定用合成小圖，不會重送真人 PII。
- `npm run review:test-live`：測試群真機測完後，從 runtime 狀態檢查 group 23 最近案件是否真的閉環。
- `npm run export:latest-test-chat`：手動補抓最新測試群聊天；正常情況下 bot 會自動保存，不需要每次跑。
- `npm run start:test`：只啟動 test dry-run 外殼，不連真 LC/TG。
- `npm run start:test:live`：只連 LiveChat 測試 group 23 + TG 測試群，不碰正式 group。它使用獨立 `test-live` lock，避免被 dry-run 外殼卡住。
- `npm run watch:test:live`：測試群 watcher；若程序異常退出會重啟，正常 `npm run stop:test:live` 不會重啟。
- `npm run preflight:test:live`：不連外，只檢查 test-live 啟動前環境、lock、direct-query 是否準備好。
- `npm run preflight:official`：檢查正式上線前是否安全：test-live/舊版 bot 必須停止、正式 group/topic 必須正確、LiveChat/TG/Anthropic/direct-query 必須可用，並檢查 Telegram 是否被其他 getUpdates 程序搶走。
- `npm run go:no-go:official`：正式上線前總檢查，串 `npm test`、路徑 gate、批量 review、真實 replay、human seeds、preflight；任一失敗即不可上線。
- `npm run postlaunch:official`：正式 bot 啟動後檢查 launchd 是否 loaded，並跑嚴格 `health:official`。
- `npm run doctor:processes`：列出本機疑似舊版/重複 bot 程序；TG 409 時先跑這個看是不是本機程序在搶 token。
- `npm run status:test:live` / `npm run stop:test:live`：查看或關閉真測試群外殼。
- `npm run status:test` / `npm run stop:test`：查看或關閉 test 外殼。
- `npm run start:official`：正式模式單次前景啟動；處理 groups 2 / 11 / 12 / 13 / 23 / 24 / 25 / 28。group 23 由同一個 official bot 處理，但 TG 只送測試群；正式 7 群仍送正式 finance group/topic。上線前必須先跑 `npm run preflight:official`。
- `npm run watch:official`：正式 watcher；若 bot 程序異常退出會自動重啟，正常 `npm run stop:official` 不會重啟。正式長時間上線建議用這個。
- `npm run health:official`：嚴格健康檢查；official 沒跑、TG 409、TG 回客戶失敗、長時間沒輪詢，都會用失敗碼回報。
- `npm run launchd:install:official`：把 official watcher 安裝成 macOS 背景服務，避免終端機關閉導致 bot 停掉。
- `npm run launchd:status:official` / `npm run launchd:uninstall:official`：查看或移除 macOS 背景服務。

## 正式上線順序

```bash
cd legacy/bot66tornado
npm run stop:test:live
npm run go:no-go:official
npm run launchd:install:official
npm run postlaunch:official
```

正式啟動後檢查：

```bash
npm run health:official
npm run launchd:status:official
```

如果要下線正式 bot：

```bash
npm run stop:official
```

如果暫時不用 launchd，也可以用 `npm run watch:official` 前景啟動；但長時間正式上線建議用 launchd，避免關終端機造成服務停止。

注意：LiveChat 預設招呼 `Hello. How may I help you?` 是 LC 後台設定，不是 bot 文案。若要主選單成為第一個客服訊息，需在 LiveChat admin 關掉該 greeting，或改成 incoming_chat webhook 模式。

## LiveChat 測試入口

測試群網址必須用 path group 格式：

```text
https://direct.lc.chat/19282375/23
```

不要用 `?group=23` 或 `/?group=23`，這兩種會被 LiveChat 分到錯的客服群。

快速開測試群：

```bash
./open-livechat.command TEST
```

## 聊天紀錄

Bot 連到 LiveChat 後，會自動保存它看過的聊天逐句紀錄：

```text
reports/livechat-transcripts/latest/<chatId>.txt
```

所以正常情況下不需要 Lucas 每次手動匯出；手動匯出只是補抓單筆或排查用。

## Runtime 狀態

runtime state 會寫在 `runtime/*-state.json`。寫入時有 lock、唯一 tmp 檔與原子 rename；如果 state JSON 壞掉，會先備份成 `.corrupt-*`，再用空狀態啟動，避免把壞檔無聲覆蓋掉。

audit 會保留在 state 裡，預設最多 10000 筆；同時會 append 到 `runtime/*-audit.ndjson`，方便事後查比較久以前的事件。可用 `BOT_AUDIT_LIMIT` 調整 state 內保留量。

## 環境變數

啟動時只讀本資料夾 `.env`，不再 fallback 到舊版資料夾。LiveChat auth 兼容舊版 `LIVECHAT_PAT` Basic token。

後台流水查詢使用本資料夾 `direct-query.js` + `env-loader.js`，不再預設讀取 `../workspace-autoreply-clean/direct-query.js`。`env-loader.js` 只是相容入口，實際解析規則來自 `src/config/env.js`。因此要獨立搬移時，整個 `bot66tornado` 資料夾可一起搬走。

`.env`、`runtime/`、`reports/` 已列入 `.gitignore`；需要的環境變數格式看 `.env.example`。專案已有 `package-lock.json`，方便之後固定部署依賴。
