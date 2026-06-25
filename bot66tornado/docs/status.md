# bot66tornado 目前狀態

更新時間：2026-06-03

## 已完成

- 建立乾淨專案資料夾：`/Users/idea3c/Documents/New project 2/bot66tornado`
- 複製產品規格 PDF：`docs/客服bot完整步驟圖.pdf`
- 搬入舊版已存在的：
  - 主選單
  - 提款第二層選單
  - 查上一筆確認選單
  - 存款未入帳收資料文案
  - 提款未收到收資料文案
  - 如何充值 SOP
  - 如何提款 SOP
  - 無法提款/流水 SOP
  - 忘記密碼 SOP
  - 查上一筆回覆資料要求
  - 真人轉接文案
  - forwarded / 等後台文案池
- 所有客戶選單按鈕都保留 emoji 作為視覺提示，方便客戶快速辨識。
- 已實作第一版純邏輯狀態機。
- 已實作等後台硬訊號三分流：
  1. 附件 / 交易硬訊號 / 身分硬訊號 → 補資料，同步同一 TG case
  2. 明確真人詞 → 轉真人
  3. 其他 → 等待進度安撫
- 已實作缺文案標記：舊版找不到的按鈕/文案不自行補，輸出 `missing_content`。
- 已補 SOP 後續路徑：
  - 充值教學後顯示舊版主選單
  - 提款教學後顯示舊版提款選單
  - 忘記密碼教學後顯示舊版主選單
  - 充值/提款教學後若客戶直接丟截圖，沿用舊版邏輯進對應存提款收件流程
  - 忘記密碼教學後若客戶仍追問，轉真人，不亂猜
- 已搬入平台設定：
  - 正式 LC group：2 / 11 / 12 / 13 / 23 / 24 / 25 / 28
  - 測試 LC group：23
  - TG finance group：`-1003181576378`
  - TG test group：`-5101503521`
  - ZAP69 topic：36735
- 已實作 runtime engine 第一版：
  - 正式模式會處理測試 group 23，但 TG 只送測試群
  - 測試模式只處理 TEST group 23
  - 狀態機結果轉成 adapter commands，不直接碰真 LC/TG
  - 存提款資料齊才產生 `telegram.send_case_card`
  - TG 只有回覆已記錄主卡/字卡才會產生 `livechat.send_staff_reply`
- 已補後台回覆後的結尾邏輯：
  - 客戶道謝 / 表示解決 → soft parked
  - 客戶補硬資料 → append 回同一 TG case
  - 客戶仍表示沒解決但不是硬資料 → 轉真人，不用模板猜
- 已實作 JSON / memory case store。
- 已新增本地 dry-run 指令：`npm run smoke`。
- 已新增路徑 gate：`npm run route:gate`，把主要窄路徑走到底，不連外。
- 已新增自動聊天紀錄落檔：
  - bot 只要成功 `get_chat`，會保存逐句紀錄到 `reports/livechat-transcripts/latest/<chatId>.txt`
  - 正常情況不需要手動匯出；手動匯出只作為補抓單筆排查
- 已新增 command runner：
  - `telegram.send_case_card` 成功後自動記錄 TG → LC mapping
  - `livechat.send_staff_reply` 若需要翻譯/潤飾但沒有 processor，會阻擋，不直接把後台英文送客戶
  - 已新增 staff reply processor：
    - 有 `ANTHROPIC_API_KEY` 時走 Anthropic 翻譯/客服化
    - 沒有 key 或 API 失敗時，使用舊版 fallback 轉成西文客服話術
    - 保留 ID / 金額 / 日期 / URL，不新增原文沒有的事實
    - LLM 結果若新增金額/參考號/URL，或把等待中改寫成已完成/已到帳，會被 deterministic fact-check 擋下並改用 fallback
- 已新增第一版 LiveChat / Telegram API adapter 檔案，並已接成可啟動 poller。
- 已接回 `direct-query.js`：
  - 預設讀取本資料夾 `direct-query.js`
  - 使用已搬入本資料夾的 `queryTurnoverRequirement`
  - 找不到舊函式時會 fallback 並轉真人，不會假裝查到結果
- 已搬入舊版圖片資產：
  - 忘記密碼平台圖
  - 充值教學圖
  - 提款教學圖
  - 無法提款 / 流水教學圖
  - 存款付款截圖範例圖
  - 提款申請截圖範例圖
- 圖片資產已加同案件去重：同一案件同一張教學/範例圖不重複送。
- LiveChat adapter 已可轉送遠端圖片：
  - 先下載舊版 LiveChat 圖片 URL
  - 上傳到 LiveChat
  - 再送 file event 給客戶
  - 若圖片失敗，fallback 送圖片 URL，不讓流程卡住
- runtime 啟動只讀本資料夾 `.env`，不再 fallback 舊版資料夾。
- LiveChat adapter 已兼容舊版 `LIVECHAT_PAT` Basic token，不強制要求 `LIVECHAT_ACCOUNT_ID`。
- 已修正「無法提款 / 流水」歸屬：
  - 這條現在是 `backend_query`，owner 是 bot，不是 TG 後台
  - 查成功後回覆流水結果
  - 查失敗時回覆舊版 fallback，並轉真人
- 已新增啟停安全外殼：
  - `npm run start:test`
  - `npm run start:test:live`
  - `npm run preflight:test:live`
  - `npm run status:test:live`
  - `npm run stop:test:live`
  - `npm run status:test`
  - `npm run stop:test`
  - `npm run start:official`
  - `npm run watch:official`
  - `npm run status:official`
  - `npm run health:official`
  - `npm run postlaunch:official`
  - `npm run doctor:processes`
  - `npm run launchd:install:official`
  - `npm run launchd:status:official`
  - `npm run launchd:uninstall:official`
  - `npm run stop:official`
  - official 模式必須 `BOT_CONFIRM_OFFICIAL=YES`，且不可 dry-run
- 已新增正式上線總檢查：
  - `npm run go:no-go:official`
  - 依序跑核心測試、主路徑 gate、批量窄路徑、真實客戶 replay、human seeds、正式 preflight
- 已新增正式啟動後檢查：
  - `npm run postlaunch:official`
  - 檢查 launchd 是否 loaded，並執行嚴格 `health:official`
- 已新增本機程序醫生：
  - `npm run doctor:processes`
  - TG 409 時用來列出本機疑似舊版/重複 bot 程序；不自動 kill
- 已新增測試群真機閉環檢查：
  - `npm run review:test-live`
  - 從 runtime 檢查 group 23 最近案件是否走到送 TG、轉真人、soft parked 或仍在等客戶補資料
- 已新增 macOS launchd 背景服務入口：
  - 避免只靠前景 terminal；`stop:official` 仍可正常停止
- 已加強 official 健康檢查：
  - official 沒跑時 `health:official` 會失敗
  - 最近 5 分鐘內 TG `getUpdates` 409 會標 unhealthy
  - TG 後台回覆送 LiveChat 失敗會標 unhealthy
  - 程序活著但長時間沒有 poll tick/audit 會標 unhealthy
- 已加強 runtime state 寫入：
  - `JsonCaseStore` 寫入 state 時使用 lock、唯一 tmp 檔與 atomic rename
  - state JSON 壞掉時會先備份 `.corrupt-*`，避免無聲覆蓋證據
  - audit state 內預設保留 10000 筆，並 append 到 `runtime/*-audit.ndjson`
- 已整理部署/倉庫衛生：
  - 新增 `.gitignore`，排除 `.env`、`runtime/`、`reports/`
  - 新增 `.env.example`
  - 新增 `package-lock.json`
  - `env-loader.js` 改成共用 `src/config/env.js` 的解析規則
- 已新增 `open-livechat.command`：
  - 測試群使用 `https://direct.lc.chat/19282375/23`
  - 明確禁止 `?group=23`，避免 LiveChat 分錯群
- 已新增第一版真 poll loop：
  - LiveChat：list chats → group 白名單 → get chat → 取客戶事件 → 新狀態機 → command runner
  - Telegram：get updates → 只吃白名單群 / topic → 只吃 reply-to 已記錄主卡 → 翻譯/潤飾 → LiveChat command
  - processed event / TG offset 已進 case store，避免重複處理

## 已驗證

`npm test` 通過 134 條測試：

- 所有客戶選單按鈕都保留 emoji
- LiveChat transcript renderer 可把真實 speaker / buttons / file 轉成可讀逐句紀錄
- menu 階段明確自由文字會進正確窄路徑或轉真人；不清楚時維持選單提醒，不因 reprompt 次數自動轉真人
- 已進入提款 / 上一筆案件 / 收資料階段時，高信心帳戶、身分、Nequi、登入恢復、驗證碼等非當前流程問題會轉真人或導回正確路徑，不用同一套關鍵字粗暴打斷所有流程
- 存款未入帳資料未齊不送 TG
- 存款截圖 + 帳號齊全才送 TG
- 提款未收到只有帳號時會問截圖
- 無法提款 / 流水走 backend query，不送 TG
- 等後台硬訊號分類
- 等後台追問不改 owner
- 等後台補資料會 append 到同一 TG case
- SOP 後用既有舊選單接續，不自行編新按鈕
- SOP 後客戶只回 OK / gracias 不打擾；若重新打 Hola / buenas 這類問候，會重送退路選單，不讓聊天停住
- 充值教學後收到截圖會進存款收件，不會掉球
- 忘記密碼教學後仍追問會轉真人，不會模板亂回
- SOP 結束、無法提款查流水入口、上一筆案件查不到、流水未完成結果會提供「改選 / 主選單 / 真人客服」退路
- 存款未到帳與提款未到帳第一次收資料時不塞退路，避免打斷正確收件流程
- bot 客戶訊息送出後，客戶 120 秒仍未回覆會送一次跟進語；客戶已回覆則不送
- bot 跟進語送出後，客戶 2 分鐘仍未回覆會送一次結束語；客戶已回覆則不送
- 客戶回覆舊跟進語後，如果 bot 又送出新的客戶訊息，會重新啟動下一輪跟進 / 結束語計時
- idle 跟進語 / 結束語遇到已關閉的 LiveChat 舊聊天時會標記 inactive，不再每輪重試拖慢 poll
- 正式平台 group/topic 對應正確
- 正式模式包含 group 23，且 group 23 的 TG 只送測試群
- engine 只在存款資料齊全後產生 TG 案件卡
- TG 後台非主卡 reply 不會轉給客戶，主卡 reply 才會轉成待翻譯/潤飾指令
- 後台回覆後，客戶道謝 soft parked；仍未解決則轉真人
- command runner 送出 TG 主卡後會記 mapping
- 沒有翻譯/潤飾 processor 時，runner 會擋下後台回覆，不讓英文原文直接出客戶端
- staff reply fallback 會把內部等待文字轉成西文客服口吻
- staff reply fallback 會保留 reference / 金額等關鍵資料
- staff reply LLM 事實檢查會擋掉新增金額或把等待中升級成已完成的回覆
- command runner 有 processor 時會送處理後文字給 LiveChat
- backend query adapter 會把流水查詢結果轉成客戶文案並送出
- official runtime 會拒絕 dry-run 或缺少 `BOT_CONFIRM_OFFICIAL=YES`
- runtime 會跳過重疊 poll tick，避免同一個 bot 自己造成 Telegram `getUpdates` 409 conflict
- official `health:official` 會在 bot 停止、TG 409、TG 回客戶失敗、長時間不輪詢時失敗
- launchd official plist 會用 official watcher 與正式環境變數，不會 dry-run
- process scanner 會辨識舊版 workspace bot 與 bot66tornado watcher，並避免把一般 npm 指令誤判成 bot
- JsonCaseStore 會保留超過 1000 筆 audit、寫 append-only audit log，並備份損壞的 state 檔
- pollLiveChat 會處理允許群組的新客戶事件，且同一事件不重複處理
- pollLiveChat / pollTelegram 外部網路例外會被 audit，不讓 runtime 直接崩掉
- pollTelegram 只接受已記錄主卡的 reply，並把後台文字轉成客戶話術
- TG 主卡送出後，後續客戶追問/補資料不會覆蓋掉 `tgMainMessageId`
- 舊版圖片資產會被掛到窄路徑，且同案件不重複發同一張
- engine 會在固定資料路徑產生 LiveChat 圖片指令
- command runner 會把圖片指令交給 LiveChat adapter
- LiveChat adapter 兼容舊版 `LIVECHAT_PAT` Basic token
- direct-query loader 可載入本資料夾 `queryTurnoverRequirement`
- direct-query loader 找不到舊檔時會安全 fallback
- JS syntax check 通過
- `npm run route:gate` 目前覆蓋 9 條主要窄路徑
- `npm run batch:path-review` 目前 146/146 通過、0 警告、0 失敗
- `npm run replay:real` 目前 14/14 通過、0 警告、0 失敗；已加嚴資料齊全未送 TG、明確自由文字卡主選單、主選單洗版、已知真人案例錯查流水/錯送 TG
- `npm run replay:human-seeds` 目前 186/186 通過、0 警告、0 失敗
- `npm run review:test-live -- --since-hours=1000` 本次檢查 10 筆測試群舊案件，0 失敗、7 警告；警告多為舊測試聊天停在等客戶補資料或 audit 已被截斷
- `npm run status:test` 可確認 test 外殼未啟動 / 是否有 lock

`npm run smoke` 目前覆蓋：

- 存款未到帳完整路徑：按鈕 → 收帳號與截圖 → 產生 TG 主卡 → 後台主卡回覆 → 客戶道謝 soft parked
- 正式模式包含 group 23，但 TG 只送測試群
- menu 階段明確自由文字會進正確窄路徑或轉真人；不清楚時維持選單提醒，不因 reprompt 次數自動轉真人
- 忘記密碼教學後仍無法登入 → 轉真人
- 無法提款 / 流水 → SOP → 收帳號 → backend query command

## 目前缺口

這些不是忘記做，而是還沒接外部系統或還沒搬完整周邊功能。

### 尚未完整真機驗證

- 以舊 runtime 檔案格式直接相容的 mapping/pending-reply 搬遷
- 真 LC/TG poll loop 已可用；正式長時間上線建議使用 `npm run launchd:install:official`，臨時前景模式才用 `npm run watch:official`

### 尚未搬完整功能

- Telegram callback 字卡：後台點「請客戶補資料 / 請客戶補截圖」

## 下一步

1. 正式長時間上線使用 `npm run launchd:install:official`，不要只靠前景 `start:official` 或 terminal。
2. 搬 Telegram callback 字卡。
3. 補以舊 runtime 檔案格式相容的 pending-reply 查詢。
