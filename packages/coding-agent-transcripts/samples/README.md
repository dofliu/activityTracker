# 樣本逐字稿

**每一個位元組都是合成的。** 沒有任何真實的提問、回應、檔案路徑、使用者名稱或機器名。
專案名一律是 `sample-project`、工作目錄一律是 `/workspace/…`、session id 一律是
`session-000N`——這些都不是任何人機器上會出現的東西。

`tests/test_samples_are_synthetic.py` 用內容掃描守著這件事：掃到家目錄形狀
（`/home/<名字>`、`/Users/<名字>`、`C:\Users\<名字>`）或金鑰樣式（`sk-`／`ghp_`／`AIza`…）
就直接失敗。要例外必須寫進白名單**並附理由**。

目錄結構刻意長得跟真的一樣（`projects/**/*.jsonl`、`local-agent-mode-sessions/*/*/local_*/…`、
`sessions/**/rollout-*.jsonl`、`<conversation>/steps/trajectory.jsonl`），
這樣 `discover()` 也測得到，不是只有 `parse()`。
