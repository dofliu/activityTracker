// web/js/core/api.js — 唯一碰 `fetch` 的地方（ADR-026，TODO D10）
//
// D10 之前有九個呼叫點繞過 `getJSON`／`postJSON` 自己寫 `fetch`，各自判斷狀態碼、各自取
// 後端的 `detail`。現在統一成三層：
//
//   request()   → 回傳原始 Response。呼叫端真的需要看 `res.status`（401 換 token、
//                 428 二次確認）或需要 `response.body`（RAG 串流）時用它。
//   getJSON()   → GET ＋ 解析 JSON，非 2xx 就丟。
//   postJSON()  → POST ＋ 解析 JSON，非 2xx 時把後端的 `detail` 當錯誤訊息。
//   sendJSON()  → 不帶 body 的 mutation（DELETE 等），語義同 postJSON。
//
// 契約測試：`web/js/` 底下除了本檔不准出現 `fetch(`。

import { API } from "./state.js";

export function request(url, { method = "GET", body, headers, ...rest } = {}) {
  const init = { method, ...rest };
  const merged = { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...(headers || {}) };
  if (Object.keys(merged).length) init.headers = merged;
  if (body !== undefined) init.body = typeof body === "string" ? body : JSON.stringify(body);
  return fetch(API + url, init);
}

async function detailOf(res) {
  try {
    return (await res.json()).detail || "";
  } catch (_) {
    return "";   // 後端沒回 JSON 時保留 HTTP fallback
  }
}

export async function getJSON(url) {
  const res = await request(url);
  if (!res.ok) throw new Error(url + " → " + res.status);
  return res.json();
}

export async function postJSON(url, body) {
  const res = await request(url, { method: "POST", body });
  if (!res.ok) throw new Error((await detailOf(res)) || (url + " → " + res.status));
  return res.json();
}

export async function sendJSON(url, method = "POST") {
  const res = await request(url, { method });
  if (!res.ok) throw new Error((await detailOf(res)) || (url + " → " + res.status));
  return res.json();
}
