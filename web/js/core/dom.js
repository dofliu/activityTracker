// web/js/core/dom.js — DOM 取值與跳脫：整個前端只有這兩個小工具是真的到處都用得到。

export const $ = (id) => document.getElementById(id);
export const esc = (t) => String(t == null ? "" : t)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
