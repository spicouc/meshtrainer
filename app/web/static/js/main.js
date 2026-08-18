// main.js — router hash lleuger + bootstrap de la UI.

import { api } from "./api.js";
import { renderDashboard } from "./dashboard.js";
import { renderDatasets } from "./datasets.js";
import { renderNewJob, renderJobsList } from "./jobs.js";
import { renderMonitor } from "./monitor.js";
import { renderLogs } from "./logs.js";
import { renderArtifacts } from "./artifacts.js";
import { renderSettings } from "./settings.js";

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

async function route() {
  if (app.dataset.cleanup) { try { app.dataset.cleanup(); } catch (e) {} delete app.dataset.cleanup; }
  if (window.__monitorES) { try { window.__monitorES.close(); } catch (e) {} window.__monitorES = null; }

  const hash = location.hash.replace(/^#/, "") || "/";
  setActiveNav(hash);
  app.scrollTop = 0;

  try {
    if (hash === "/" || hash === "") return renderDashboard(app);
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
    // 404
    app.innerHTML = "";
    app.append(Object.assign(document.createElement("div"), { className: "empty-state" }));
    app.lastChild.innerHTML =
      `<div class="big">🧭</div><p>Page not found</p>` +
      `<a class="btn btn-primary" href="#/">Back to dashboard</a>`;
  } catch (err) {
    // mai pantalla blanca (punt 27)
    app.innerHTML = "";
    const box = Object.assign(document.createElement("div"), { className: "card" });
    box.innerHTML = `<h2>Something went wrong</h2>
      <p class="dim">${String(err.message || err).replace(/[<>&]/g, (c) => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]))}</p>
      <a class="btn" href="#/">Back to dashboard</a>`;
    app.append(box);
  }
}

async function bootstrap() {
  // estat de l'API (dot a la topbar)
  const statusEl = document.getElementById("api-status");
  async function ping() {
    try {
      await api("/api/health");
      statusEl.innerHTML = '<span class="dot dot-ok"></span> online';
    } catch (e) {
      statusEl.innerHTML = '<span class="dot dot-err"></span> API offline';
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
