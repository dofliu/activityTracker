# ADR-027：程序內可變狀態收成可注入的 store——測試不再需要「重設鉤子」

- 狀態：**Accepted**（2026-09-17 起草並於同日實作，TODO D11）
- 關聯：[ADR-008](ADR-008-gated-agent-executor.md) 分級執行器與一次性確認碼、[ADR-014](ADR-014-multi-channel-push-and-arm-code.md) 一次性解鎖碼、[ADR-026](ADR-026-frontend-modules.md) 前端模組化（`state.js` 的過渡形狀）
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.4 第 8 點與 §4.3

## Context

服務裡有一批**模組層的可變全域**，散在五個模組：

| 在哪 | 是什麼 | 誰在保護它 |
| :--- | :--- | :--- |
| `core/agent_executor.py` | `_PENDING_L2_CONFIRMS`——L2 一次性確認碼的雜湊與到期時間 | 無鎖 |
| `notifiers/telegram_approvals.py` | `_ARMED_UNTIL`、`_PENDING_ARM_CODE`、`_PROCESSED_CALLBACK_IDS`、poller 觀測值 | 一個模組層 `Lock` |
| `notifiers/telegram_chat.py` | `_ASK_IN_FLIGHT`、`_ASKS_ANSWERED` | 一個模組層 `Lock` |
| `core/secretary/greeting.py` | `_LLM_CACHE`——問候卡的 LLM 文字快取 | 一個模組層 `Lock` |
| `core/secretary/aggregate.py` | `_cache`——advisor 摘要快取 | 物件內的 `Lock` |
| `core/project_engine.py` | `_PROJECT_CACHE`、`_LAST_PROJECT_REFRESH_TIME` | 一個 `RLock` |

它們**不是設計失誤**——其中四項刻意只存在記憶體（重啟即歸零是 ADR-008／ADR-014 的安全性質），另兩項是為了擋住前端每 4 秒的輪詢。問題不在「有狀態」，在**沒有把手**：

1. **測試只能靠重設鉤子**。`_reset_state_for_tests()`（兩個）、`_reset_llm_cache_for_tests()`、
   `_reset_pending_confirms()`、`reset_advisor_cache()`——五個只為測試存在的函式，而且它們是
   **產品程式碼的一部分**：出現在 wheel 裡、任何人都能呼叫。更糟的是它們**只清得掉自己記得要清的欄位**，
   漏一個就是測試之間互相污染，而症狀是「單獨跑會過、一起跑會壞」。
2. **一個程序只能有一份**。要寫「兩個使用者的 arm 狀態互不影響」這種測試，沒有辦法表達。
3. **沒人說得出「現在有哪些程序內狀態」**。要回答這個問題得 grep 五個模組。

另有一個**不同性質**的問題，但根因相同——`core/server.py` 在 **import 時**算好 CORS 允許來源：

```python
_startup_cfg = get_config()
_allowed_origins = configured_allowed_origins(_startup_cfg)   # 這一行凍結在 import 時刻
app.add_middleware(CORSMiddleware, allow_origins=_allowed_origins, ...)
```

而同一份設定在 `enforce_local_security_boundary` middleware 裡是**每個請求重讀**的。結果是：使用者在
儀表板改了 `security.allowed_origins` 並存檔，安全邊界 middleware 立刻生效、CORS 標頭卻還是舊的，
**必須重啟服務**——而沒有任何地方寫著這件事。

## Decision

### 1. 一個 `core/runtime_state.py`，一組具名 store

把六處狀態收成五個小類別，全部住在一個模組：

```python
ConfirmStore    # L2 一次性確認碼（issue / verify / discard / clear）
ApprovalState   # Telegram 批准通道：arm 視窗、一次性 arm code、callback 去重、poller 觀測
ChatState       # Telegram 對話：in-flight 與已回答計數
TtlCache        # 單鍵 TTL 快取（問候卡 LLM 文字與 advisor 摘要共用這一個）
ProjectCache    # 專案清單的 TTL 快取
```

每個類別**自帶自己的鎖**——鎖跟著它保護的狀態走，不再是一個模組層 `Lock` 保護一堆不相干的變數。

`RuntimeState` 把五個 store 組成一個容器；`runtime_state()` 回傳**行程預設實例**，
`new_runtime_state()` 給測試一份全新的。

### 2. 呼叫端用注入，預設仍是行程實例

碰到狀態的函式加一個具名參數：

```python
def arm_approvals(cfg=None, now=None, *, state: ApprovalState | None = None): ...
```

不給就用行程預設——**產品行為與現在一字不差**；測試給一個新的 `ApprovalState()`，
於是**五個重設鉤子全部刪掉**。

這裡要誠實：行程預設仍然是一個 process-wide 單例，因為它本來就該是——一個服務程序的
arm 視窗只有一個。變的是三件事：它**有名字**、它**能被替換**、以及**測試不再需要去動線上那一份**。
「模組全域」與「可注入的 process default」的差別不在有沒有全域，在**有沒有把手**。

### 3. 狀態的安全性質一個都不放寬

- 這些 store **只存在記憶體**：不落庫、不寫檔、不進 log、不跨程序共享。重啟歸零仍然是
  ADR-008／ADR-014 明說的安全性質，而不是實作細節。
- `ConfirmStore` 與 `ApprovalState` 只存**雜湊**與到期時間，never the code itself——與搬家前相同。
- 契約測試盯著這件事：`core/runtime_state.py` 不得出現 `open(`／`session_scope`／`requests`／
  `subprocess`；`RuntimeState` 不得被序列化進任何回應。

### 4. CORS 改成每個請求看當下的設定

`DynamicCorsMiddleware` 是 Starlette `CORSMiddleware` 的薄子類：每次請求先比對
`configured_allowed_origins(get_config())` 與上次建構用的清單，**只有在變了的時候**才用公開建構子
重算一次衍生標頭。沒變就是一次 list 比較，量測不到的成本。

於是 `POST /api/v1/config` 改 `security.allowed_origins` **不必重啟就生效**，與同一份設定在安全邊界
middleware 的行為一致。這不是放寬邊界——允許清單仍然只來自設定檔，而且 loopback 限制與 extension
token 邊界完全沒動；改的只是「什麼時候讀」。

### 5. 不做的事

- **不動 `core/agent_dispatch.py` 的 `_RUNNING` 與 `core/server.py` 的 `_EXTENSION_TOKEN_WARNING_STATE`**。
  前者是「這個程序現在有哪些執行緒在跑」，後者是 log 節流計數——兩者都**本質上**綁在程序上、
  沒有測試鉤子、也沒有人需要替換它們。搬過去只會讓 `runtime_state` 變成雜物櫃。
- **不動前端的 `web/js/core/state.js`**。ADR-026 說「改成注入是 D11 的事」——這一輪沒做，
  理由寫在下面的 Consequences，不假裝做完了。

## Consequences

**好的**：五個只為測試存在的函式從產品程式碼消失；「這個程序有哪些可變狀態」有一個檔案可以一次讀完；
每個 store 的鎖與它保護的資料在同一個類別裡；測試之間不再靠「記得呼叫重設」互不污染；
改 `allowed_origins` 不再需要重啟而且沒有人需要知道這件事。

**代價**：

- **多一層參數**。每個碰狀態的函式多一個 `state=None`。換到的是那五個鉤子與它們的踩坑。
- **行程預設仍是單例**。這不是完全的依賴注入；要做到「每個請求一份」需要把 store 放進
  FastAPI 的 `app.state` 並讓所有呼叫端拿 `Request`——那會擴散到與狀態無關的層，代價大於收益。
  如實記在這裡：D11 買到的是「可注入」，不是「無全域」。
- **前端的 `state.js` 還在**。ADR-026 承諾「D11 改成注入」，這一輪**沒有兌現**：前端的 49 個
  共享值全部經由渲染函式的閉包讀寫，要改成注入等於重寫十個分頁模組的函式簽章，而且
  沒有等價於 pytest 的前端測試可以證明「行為不變」——只有一支 Playwright 煙霧測試。
  在能證明之前不動它，比動了再說「應該沒壞」誠實。它留在檢視報告 §4.4，不改狀態為已完成。
