// main.js — router hash lleuger + bootstrap de la UI.

import { api } from "./api.js";
import { el, clear } from "./ui.js";
import { renderDashboard } from "./dashboard.js";
import { renderDatasets } from "./datasets.js";
import { renderNewJob, renderJobsList } from "./jobs.js";
import { renderMonitor } from "./monitor.js";
import { renderLogs } from "./logs.js";
import { renderArtifacts } from "./artifacts.js";
import { renderSettings } from "./settings.js";
import { renderSetup } from "./setup.js";

const app = document.getElementById("app");

function setActiveNav(route) {
  document.querySelectorAll(".nav-link").forEach((a) => {
    const nav = a.dataset.nav;
    a.classList.toggle("active",
      (nav === "dashboard" && route === "/") ||
      (nav === "new" && route.startsWith("/jobs/new")) ||
      (nav === "datasets" && route.startsWith("/datasets")) ||
      (nav === "settings" && route.startsWith("/settings")));
  });
}

// v1.4.1 (P1 route cleanup, FT-20): lifecycle explícit — cada pantalla que
// creï recursos (EventSource, intervals, timers, listeners) ha de registrar
// una funció de neteja a window.__currentCleanup. El router la crida
// SEMPRE abans de renderitzar una ruta nova.
async function runCleanup() {
  const fn = window.__currentCleanup;
  if (typeof fn === "function") {
    try { fn(); } catch (e) { /* la neteja mai ha de trencar la navegació */ }
    window.__currentCleanup = null;
  }
  // seguretat extra: cap EventSource/intervall orfe d'una vista anterior
  if (window.__monitorES) {
    try { window.__monitorES.close(); } catch (e) {}
    window.__monitorES = null;
  }
}

async function route() {
  await runCleanup();

  const hash = location.hash.replace(/^#/, "") || "/";
  setActiveNav(hash);
  app.scrollTop = 0;

  try {
    if (hash === "/" || hash === "") {
      // ── Phase 3 (punt 5): first-run setup ─────────────────────────────
      try {
        const st = await api("/api/setup/status");
        if (st && st.first_run) return renderSetup(app);
      } catch (e) { /* si l'API no respon, dashboard amb error visible */ }
      return renderDashboard(app);
    }
    if (hash.startsWith("/jobs/new")) return renderNewJob(app);
    if (hash.startsWith("/datasets")) return renderDatasets(app);
    if (hash.startsWith("/settings")) return renderSettings(app);
    const m = hash.match(/^\/jobs\/([^/]+)$/);
    if (m) return renderMonitor(app, m[1]);
    const ml = hash.match(/^\/jobs\/([^/]+)\/logs$/);
    if (ml) return renderLogs(app, ml[1]);
    const ma = hash.match(/^\/jobs\/([^/]+)\/artifacts$/);
    if (ma) return renderArtifacts(app, ma[1]);
    if (hash.startsWith("/jobs")) return renderJobsList(app);
    // 404 (v1.4.1 P0 XSS: DOM API, mai innerHTML)
    clear(app);
    const empty = el("div", { class: "empty-state" }, [
      el("div", { class: "big" }, ["🧭"]),
      el("p", {}, ["Page not found"]),
      el("a", { class: "btn btn-primary", href: "#/" }, ["Back to dashboard"]),
    ]);
    app.append(empty);
  } catch (err) {
    // mai pantalla blanca (punt 27) — v1.4.1: textContent, no innerHTML
    clear(app);
    const box = el("div", { class: "card" }, [
      el("h2", {}, ["Something went wrong"]),
      el("p", { class: "dim" }, [String(err.message || err)]),
      el("a", { class: "btn", href: "#/" }, ["Back to dashboard"]),
    ]);
    app.append(box);
  }
}

async function bootstrap() {
  // estat de l'API (dot a la topbar) — v1.4.1: DOM API
  const statusEl = document.getElementById("api-status");
  async function ping() {
    const dot = el("span", { class: "dot" });
    try {
      await api("/api/health");
      dot.className = "dot dot-ok";
      statusEl.replaceChildren(dot, document.createTextNode(" online"));
    } catch (e) {
      dot.className = "dot dot-err";
      statusEl.replaceChildren(dot, document.createTextNode(" API offline"));
    }
  }
  ping();
  setInterval(ping, 10000);

  try {
    const s = await api("/api/settings");
    document.getElementById("footer-env").textContent = `APP_ENV=${s.app_env}`;
  } catch (e) { /* noop */ }

  window.addEventListener("hashchange", route);
  await route();
  // focus al main per navegació per teclat (punt 25)
  app.setAttribute("tabindex", "-1");
}

bootstrap();
