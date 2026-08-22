// api.js — client REST + SSE del MeshTrainer App API (Phase 2).
// La UI només parla amb l'Application API; mai importa core Python.

const API_BASE = "";  // mateix origen (FastAPI serveix UI + API)

export async function api(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (opts.raw) return res;
  let body;
  try { body = await res.json(); } catch { body = null; }
  if (!res.ok || (body && body.ok === false)) {
    const msg = (body && body.error && body.error.message) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.code = body && body.error && body.error.code || "http_error";
    err.status = res.status;
    throw err;
  }
  return body.data;
}

export function uploadDataset(file) {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`${API_BASE}/api/datasets/upload`, { method: "POST", body: fd })
    .then(async (res) => {
      const body = await res.json();
      if (!res.ok || body.ok === false) {
        const msg = (body.error && body.error.message) || `HTTP ${res.status}`;
        throw new Error(msg);
      }
      return body.data;
    });
}

// SSE amb cursor estable (contracte E0-04): id=seq rowid, event, data.
// Tots els events s'emeten com a "message" (el tipus viatja dins del data)
// perquè EventSource només dispara onmessage per events sense nom propi.
// v1.4.1 (P1): connecta amb ?after_seq=<highest> (cursor explícit) a més
// del Last-Event-ID automàtic del navegador; el servidor mai reenvia
// seq <= highest_seq del client (0 duplicats / 0 perduts en reconnect).
export function connectSSE(jobId, { onEvent, onStatus, onError, afterSeq } = {}) {
  let url = `${API_BASE}/api/jobs/${jobId}/events`;
  const seq = (typeof afterSeq === "function" ? afterSeq() : afterSeq) || 0;
  if (seq > 0) url += `?after_seq=${seq}`;
  const es = new EventSource(url);
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      data.seq = Number(ev.lastEventId || data.seq || 0);
      onEvent && onEvent(data);
    } catch (e) { /* ignora */ }
  };
  es.onopen = () => onStatus && onStatus("open");
  es.onerror = () => onStatus && onStatus("reconnecting");
  return es;
}

export function fmtBytes(n) {
  if (n == null || isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function fmtDur(seconds) {
  if (seconds == null || isNaN(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  return `${h}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleTimeString("ca-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function escapeHtml(s) {
  // Sempre text, mai HTML cru (punt 23: XSS).
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
