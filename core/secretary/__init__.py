"""小秘書：訊號 → 聚合 → 呈現，另有一層記憶（ADR-024，TODO D8）。

2026-09-16 之前這些程式是 `core/` 底下十一個平輩模組，彼此的關係只能讀原始碼推，
而且靠 116 處函式內延遲 import 撐著一個真實的循環依賴。現在分成四層，方向只准往下：

    types    兩個定型契約（Signal／Proposal）
    memory   筆記／偏好／決定／觀察／個人檔案——只讀寫自己的表
    signals  從既有 domain 模組收集訊號，正規化成 Signal
    aggregate 把 Signal 變成 Proposal：加權、去重、排序、上限
    present  給人看的：今日首頁、回答（greeting.py 問候卡、packs.py 早晨包同層）

下層永遠不 import 上層；需要上層資料時由呼叫端注入（例如記憶脈絡要帶 top 提案，
是呼叫端把提案傳進來，不是 memory 自己去 import aggregate）。

`core/agent_executor.py` 不屬於這四層：它在 aggregate 之上、present 之下，
可以 import aggregate，永遠不被 aggregate import。
"""
