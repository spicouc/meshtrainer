// ui.js — helpers DOM, toasts, badges, renders comuns.

import { escapeHtml, fmtBytes, fmtDur, fmtTime } from "./api.js";

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v; // només amb contingut intern controlat
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2), v);
    } else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function toast(msg, kind = "info") {
  const region = document.getElementById("toast-region");
  if (!region) return;
  const t = el("div", { class: `toast toast-${kind}`, role: "status" }, [msg]);
  region.append(t);
  setTimeout(() => t.remove(), 5000);
}

export function statusBadge(status) {
  const s = String(status || "?").toLowerCase();
  return `<span class="badge badge-${s}">${escapeHtml(status)}</span>`;
}

export function kpi(label, value, sub = "") {
  return el("div", { class: "kpi" }, [
    el("div", { class: "label" }, [label]),
    el("div", { class: "value" }, [value]),
    sub ? el("div", { class: "sub" }, [sub]) : null,
  ]);
}

export function progressBar(fraction, label) {
  const pct = Math.max(0, Math.min(100, Math.round((fraction || 0) * 100)));
  const bar = el("div", { class: "progress-bar", role: "progressbar",
                          "aria-valuenow": String(pct), "aria-valuemin": "0",
                          "aria-valuemax": "100", "aria-label": label || "progress" },
                 [el("div", { class: `progress-fill${pct >= 100 ? " done" : ""}`,
                              style: `width:${pct}%` })]);
  return bar;
}

export function emptyState(icon, title, ctaHtml = "") {
  return el("div", { class: "empty-state" }, [
    el("div", { class: "big" }, [icon]),
    el("p", { class: "dim" }, [title]),
    ctaHtml,
  ]);
}

export function alertBox(kind, message, detail = "") {
  return el("div", { class: `alert alert-${kind}` }, [
    el("strong", {}, [message]),
    detail ? el("div", { class: "error-detail" }, [detail]) : null,
  ]);
}

// errors comprensibles (punt 12): mai tracebacks/raw repr.
export function friendlyError(err) {
  const code = err.code || "";
  const map = {
    not_found: "No trobat",
    invalid_state: "Operació no permesa en aquest estat",
    validation_failed: "La validació ha fallat",
    backend_unavailable: "Backend no disponible",
    artifact_unavailable: "Artefacte no disponible",
    forbidden: "Accés denegat",
  };
  const head = map[code] || (err.message ? err.message : "Error de l'API");
  return { head, code };
}
