# ADR-028：驗收中心改成「一個 reading ＋ 一張階梯表」

- 狀態：**Accepted**（2026-09-18 起草並於同日實作，TODO D12）
- 關聯：[ADR-016](ADR-016-acceptance-center.md) 驗收中心與它的 claim boundary、[ADR-024](ADR-024-secretary-layers.md) 分層與方向只准往下、[ADR-027](ADR-027-injected-runtime-state.md) 同一輪的「把隱性結構變成有名字的東西」
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.4 第 5 點、[docs/TODO.md](TODO.md) D12

## Context

`core/acceptance.py` 是 1,560 行，其中 **992 行是 22 個手寫的 `_check_aN`**，另有 201 行是
`_ITEMS` 中繼資料表。每個 `_check_aN` 都長成同一個樣子：

```python
def _check_a3(ctx):
    receipts = _receipts(ctx, approved_via="telegram_inline")
    ok = [r for r in receipts if r["status"] == "succeeded"]
    evidence = { ... }
    if ok:
        return {"status": PASSED, "detail": f"已有 {len(ok)} 筆…", "evidence": evidence}
    if receipts:
        return {"status": PARTIAL, "detail": "有收據但沒有成功的…", "evidence": evidence}
    if not evidence["approvals_enabled"]:
        return {"status": NOT_CONFIGURED, "detail": "inline 批准預設關閉…", "evidence": evidence}
    return {"status": PENDING, "detail": "批准通道已開，還沒批過任何一筆。", "evidence": evidence}
```

**流程 22 份都一樣**（查資料 → 組 evidence → 依序判定），所以那些 `if/return` 是重複，不是內容。
真正的內容只有兩件事：**去查什麼**，以及**查到什麼就算什麼**。它們混在一起帶來三個具體問題：

1. **判準看不見**。「A13 在什麼情況下會是 partial？」要讀完 40 行程式才答得出來，而那 40 行
   有一半是 `return {"status": ..., "detail": ..., "evidence": evidence}` 的樣板。
2. **順序是隱性的**。後面那條分支之所以到得了，是因為前面的都不成立——這個事實只存在於
   閱讀者的腦中，沒有任何東西守著它。少寫一個 `return`、把兩個 `if` 對調，測試不一定會紅。
3. **加一項要記得整套協定**。新增一項驗收要自己記得回傳三個鍵、記得最後一定要有 fallback、
   記得狀態只能用那七個常數。忘了就是執行期才發現。

## Decision

### 1. 一項驗收 = 一個 reading ＋ 一張階梯

```python
{
    "id": "A3",
    "title": "Telegram 設定 + inline 批准",
    "how": "設定 Telegram → 開 inline 批准 → 解鎖 → 實批一次 L1 動作",
    "criterion": "有 approved_via=telegram_inline 的成功 receipt",
    "probe": Ladder(r.a3_telegram_inline, (
        (has("succeeded"), PASSED, lambda x: f"已有 {x.evidence['succeeded']} 筆…的成功收據。"),
        (has("telegram_inline_receipts"), PARTIAL, "有 telegram_inline 收據但沒有成功的；看收據的 error_code。"),
        (missing("approvals_enabled"), NOT_CONFIGURED, "inline 批准預設關閉（需執行器與批准通道兩個開關都開）。"),
        (OTHERWISE, PENDING, "批准通道已開，還沒批過任何一筆。"),
    )),
}
```

- **reading**（`readings.py`）只回答「去查什麼」，回傳一個 `Reading`，**不判定**。
- **階梯**（`items.py`，與規格同一列）回答「查到什麼就算什麼」：由上往下，第一條成立的說了算。
- `Ladder` 是十行的直譯器（`rules.py`）：跑 reading、走階梯、組出那三個鍵。

### 2. `evidence` 與 `facts` 分開

`Reading` 有兩格。`evidence` 是**會回到 API 與畫面上**的那份字典——鍵、順序與值都是對外契約。
`facts` 只給階梯用。

這個區分不是潔癖，是被現況逼出來的：A8 的 evidence 給的是「最近一筆收據」，但敘述要的是
「最近一筆**成功**的收據」——最新那筆失敗時兩者不同；A15 的 evidence 只留最近 7 天，敘述卻要
第一天；A17 的 `tone_label` 刻意不對外。**沒有 `facts` 的話，為了讓規則寫得出來就得偷偷往
evidence 加欄位——那就是改了對外契約還說自己沒改。**

### 3. 提早回傳仍然保留

功能關掉時不做昂貴的收集（A16／A19／A20／A22 的 `not_configured`、A6 的 `runtime_only`、
A21 的「正在回收中」）：reading 直接回一份只有開關的 `Reading`，由階梯的第一列接住。
與 D12 之前的 `if ... return` 逐列相同，包括**只有那條分支才有的 evidence 形狀**。

### 4. 拆成套件，方向只准往下

```
core/acceptance/__init__.py   公開介面與 claim boundary（74 行）
core/acceptance/rules.py      狀態字彙、Reading、Ladder、共用查詢（198 行）
core/acceptance/readings.py   22 個「去查什麼」（585 行）
core/acceptance/items.py      規格 ＋ 階梯（399 行）
core/acceptance/report.py     人工署名、release gate、對外入口（249 行）
```

`items → readings → rules`、`report → items/rules`，由契約測試掃 import 把關（同 ADR-024）。

### 5. 表格的形狀本身有測試守著

`tests/test_acceptance_declarative.py` 守的是**機構**而不是某一項的答案：22 項全部是 `Ladder`
（沒有人偷塞自訂函式）、每個階梯都以 `OTHERWISE` 收尾且 `OTHERWISE` 只能在最後、狀態只能來自
那七個常數、`attested` 不准出現在表格裡、沒有沒人用的 reading、沒有兩項共用同一個 reading、
沒有任何檔案超過 600 行。**這些在 D12 之前全部無法表達**——它們是「一堆函式」變成「一張表」
才買得到的東西。

## Consequences

**好的**：

- 「A13 在什麼情況下會是 partial」現在是表格上的一列，不必讀程式。
- 順序這件事從「讀者要自己記得」變成「測試會擋」。
- 新增一項驗收＝寫一個 reading、加一列；忘記 fallback 會被測試擋下，回傳形狀不可能寫錯。
- 每個檔案都不超過 600 行，而且**測試禁止它們長回去**。

**代價，如實記下**：

- **總行數幾乎沒有變少：1,560 → 1,505（五個檔案）。** 這一輪省掉的是約 250 行重複的
  `if/return` 樣板，但那些行數被表格的結構吃回去了。會這樣是因為**內容本身不可壓縮**：22 份
  中文規格、22 個 evidence 組裝、約 80 句分支敘述——要讓數字真的掉下來只能砍掉敘述或 evidence
  欄位，那是改行為，不是整頓。D12 的收據（單檔 < 600 行）是**靠拆檔**達成的，不是靠變短；
  把這件事寫在這裡，比在 PR 裡宣稱「大幅精簡」誠實。
- **一項驗收現在住在兩個地方**：reading 在 `readings.py`、規格與階梯在 `items.py`。要完整理解
  一項得看兩處。換到的是「規格與判準並排」與上面那些可測的性質。
- **表格裡仍有 lambda**。需要內插數字的敘述、以及少數判準（A6 的「載入的是舊索引」、A18 的
  「有焦點也有記憶」）就是條件式，硬塞進 `has()`／`missing()` 只會做出一個九個參數的通用探針。
  真實比例：**73 條規則裡有 8 條的判準用到 lambda**，集中在 6 項（A6 三條，A7／A15／A18／A21／A22
  各一條）；**另外 16 項的判準全部用具名述詞寫得完**。敘述那一欄則有約半數是 f-string 函式，
  因為它們本來就要把查到的數字念出來。不宣稱「全部宣告式」。

## 怎麼證明行為沒變

`pytest` 全綠不足以證明——它只走得到本機資料庫剛好產生的那些分支。這一輪的證據是
**差分收據**：把改動前的 `core/acceptance.py` 原封不動載成第二個模組，用 **72 個合成狀態**
（每一項的每一條分支各至少一次，含 runtime、停用、失敗、正在進行中、舊索引…）同時餵給
改動前與改動後，比對整份報告的 JSON——**鍵的順序也算**。72 個全部逐位元組相同。

harness 自己也驗過會壞：蓄意把 `:.2%` 改成 `:.1%`、把 A21 的兩個判斷對調、把 `or not result`
拿掉，三種都被抓出來。腳本與情境檔留在 `docs/ADR-028-declarative-acceptance.md` 描述的形狀下，
不進 repo（它需要改動前的檔案才能跑），但重現方式寫在 ROADMAP §11.2 的收據裡。
