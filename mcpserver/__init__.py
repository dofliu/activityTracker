"""唯讀 MCP Context Server（[ADR-032](../docs/ADR-032-readonly-mcp-context-server.md)）。

這個套件是被 Claude Code／Codex 之類的 agent **當子程序啟動**的 stdio MCP server，
把本專案的 canonical context 以唯讀方式暴露出去。三條貫穿全套件的鐵律：

1. **唯讀在引擎層成立，不是靠自律**：`readers` 自己開 ``?mode=ro`` 連線，
   從不呼叫 ``get_db()``／``Database()``／``session_scope()``——那三個一碰就會跑
   migration、寫備份、下 ``PRAGMA journal_mode=WAL``（ADR-032 Context 陷阱 1／2）。
2. **不轉送 core 的既有產物**：每個 tool 的輸出欄位是 `tools` 裡寫死的白名單。
   既有 handoff 產物帶本機絕對路徑與 AI 對話原文，原樣送出去就是 D4 的反例。
3. **只有 `server` 能 import 官方 SDK**：其餘五個模組在沒裝 ``[mcp]`` 的環境也
   import 得起來、測得起來（CI 的 `test-core-without-rag-extra` job 就是實測環境）。

**本套件不得有子目錄**：契約測試沿用 `tests/test_acceptance_center.py` 的掃描範本，
那個範本用的是 ``package.glob("*.py")``——只掃一層。開了子目錄，所有安全掃描都會安靜地漏掉它。
"""

__all__ = []
