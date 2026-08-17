// settings.js — informació de configuració i sistema (sense secrets).

import { api, escapeHtml, fmtBytes } from "./api.js";
import { el, clear, kpi, alertBox } from "./ui.js";

export async function renderSettings(main) {
  clear(main);
  main.append(el("h1", {}, ["Settings"]));

  let [settings, system, backends] = await Promise.all([
    api("/api/settings").catch(() => null),
    api("/api/system").catch(() => null),
    api("/api/backends").catch(() => null),
  ]);

  const grid = el("div", { class: "grid grid-2" });
  main.append(grid);

  // ── app ───────────────────────────────────────────────────────────────
  const appCard = el("div", { class: "card" }, [el("h2", {}, ["Application"])]);
  if (settings) {
    const table = el("table", {}, [el("tbody")]);
    const rows = [
      ["API bind", settings.api_bind],
      ["APP_ENV", settings.app_env],
      ["Storage", settings.storage_dir],
      ["Database", settings.db_path],
      ["Admin token", settings.admin_token_configured ? "configured" : "not configured"],
    ];
    for (const [k, v] of rows) {
      table.querySelector("tbody").append(el("tr", {},
        [el("td", { class: "dim" }, [k]), el("td", { class: "mono small" }, [escapeHtml(String(v))])]));
    }
    appCard.append(table);
  } else {
    appCard.append(alertBox("error", "API offline", ""));
  }
  grid.append(appCard);

  // ── system ────────────────────────────────────────────────────────────
  const sysCard = el("div", { class: "card" }, [el("h2", {}, ["System"])]);
  if (system) {
    const k = el("div", { class: "grid grid-4" });
    k.append(
      kpi("CPU", `${system.cpu?.cores ?? "—"} cores`),
      kpi("RAM", fmtBytes(system.ram?.used), `${fmtBytes(system.ram?.total)} total`),
      kpi("Swap", fmtBytes(system.swap?.used), `${fmtBytes(system.swap?.total)} total`),
      kpi("Disk", fmtBytes(system.disk?.free), `${fmtBytes(system.disk?.total)} total`),
    );
    sysCard.append(k);
  } else {
    sysCard.append(alertBox("error", "System info no disponible", ""));
  }
  grid.append(sysCard);

  // ── backends ──────────────────────────────────────────────────────────
  const bkCard = el("div", { class: "card" }, [el("h2", {}, ["Backends"])]);
  if (backends) {
    const table = el("table", {}, [el("thead", {}, [el("tr", {}, [
      "Backend", "Available", "Model"
    ].map((h) => el("th", {}, [h])))]), el("tbody")]);
    for (const b of backends) {
      table.querySelector("tbody").append(el("tr", {}, [
        el("td", {}, [escapeHtml(b.display_name || b.id)]),
        el("td", { html: b.available
          ? '<span class="badge badge-pass">available</span>'
          : '<span class="badge badge-fail">unavailable</span>' }),
        el("td", { class: "dim" }, [escapeHtml(b.model_constraints?.default_model || "")]),
      ]));
    }
    bkCard.append(table);
  }
  grid.append(bkCard);
}
