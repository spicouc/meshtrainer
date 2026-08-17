// dashboard.js — pantalla principal: system, backends, workers, jobs.

import { api, escapeHtml, fmtBytes, fmtDur, fmtTime } from "./api.js";
import { el, clear, kpi, statusBadge, emptyState, progressBar, toast } from "./ui.js";

export async function renderDashboard(main) {
  clear(main);
  const grid = el("div", { class: "grid stack" });
  main.append(grid);

  // ── system ────────────────────────────────────────────────────────────
  const sysCard = el("div", { class: "card" }, [el("h2", {}, ["System"])]);
  const sysGrid = el("div", { class: "grid grid-4" });
  sysCard.append(sysGrid);
  grid.append(sysCard);

  // ── backends ──────────────────────────────────────────────────────────
  const bkCard = el("div", { class: "card" }, [el("h2", {}, ["Backends"])]);
  const bkGrid = el("div", { class: "grid grid-2" });
  bkCard.append(bkGrid);
  grid.append(bkCard);

  // ── jobs resum + recents ──────────────────────────────────────────────
  const jobsCard = el("div", { class: "card" }, [el("h2", {}, ["Jobs"])]);
  const jobsKpi = el("div", { class: "grid grid-4" });
  jobsCard.append(jobsKpi);
  grid.append(jobsCard);

  const recentCard = el("div", { class: "card" }, [el("h2", {}, ["Recent jobs"])]);
  grid.append(recentCard);

  // ── accions ràpides ───────────────────────────────────────────────────
  const actions = el("div", { class: "flex" }, [
    el("a", { href: "#/jobs/new", class: "btn btn-primary" }, ["＋ New Training Job"]),
    el("a", { href: "#/datasets", class: "btn" }, ["＋ Add Dataset"]),
  ]);
  main.append(actions);

  // dades en paral·lel
  const [sys, bks, jobs, dss] = await Promise.all([
    api("/api/system").catch(() => null),
    api("/api/backends").catch(() => null),
    api("/api/jobs").catch(() => null),
    api("/api/datasets").catch(() => null),
  ]);

  // system
  if (sys) {
    sysGrid.append(kpi("CPU", `${sys.cpu?.cores ?? "—"} cores`));
    sysGrid.append(kpi("RAM", fmtBytes(sys.ram?.used), `${fmtBytes(sys.ram?.total)} total`));
    sysGrid.append(kpi("Swap", fmtBytes(sys.swap?.used), `${fmtBytes(sys.swap?.total)} total`));
    sysGrid.append(kpi("Disk free", fmtBytes(sys.disk?.free), `${fmtBytes(sys.disk?.total)} total`));
  } else {
    sysGrid.append(emptyState("⚠️", "API offline", ""));
  }

  // backends
  if (bks) {
    for (const b of bks) {
      const card = el("div", { class: "kpi" }, [
        el("div", { class: "label" }, [escapeHtml(b.display_name || b.id)]),
        el("div", { class: "value" }, [
          b.available
            ? el("span", { style: "color:var(--ok)" }, ["● available"])
            : el("span", { style: "color:var(--err)" }, ["● unavailable"]),
        ]),
        el("div", { class: "sub" }, [b.model_constraints?.default_model || b.id]),
      ]);
      bkGrid.append(card);
    }
  } else {
    bkGrid.append(emptyState("⚠️", "No backends", ""));
  }

  // jobs resum
  const count = (st) => (jobs || []).filter((j) => j.status === st).length;
  if (jobs) {
    jobsKpi.append(kpi("RUNNING", count("RUNNING")));
    jobsKpi.append(kpi("COMPLETED", count("COMPLETED")));
    jobsKpi.append(kpi("FAILED", count("FAILED")));
    jobsKpi.append(kpi("CANCELLED", count("CANCELLED")));
  } else {
    jobsKpi.append(emptyState("⚠️", "API offline", ""));
  }

  // recents
  const table = el("table", {}, [
    el("thead", {}, [el("tr", {}, [
      "Name", "Backend", "Model", "Status", "Progress", "Started", "Duration"
    ].map((h) => el("th", {}, [h])))]),
  ]);
  const tbody = el("tbody");
  table.append(tbody);
  if (jobs && jobs.length) {
    for (const j of jobs.slice(0, 10)) {
      const started = j.started_at ? new Date(j.started_at) : null;
      const dur = started && j.finished_at
        ? (new Date(j.finished_at) - started) / 1000 : null;
      tbody.append(el("tr", {}, [
        el("td", {}, [el("a", { href: `#/jobs/${j.job_id}` }, [escapeHtml(j.name)])]),
        el("td", {}, [escapeHtml(j.backend_id)]),
        el("td", { class: "dim" }, [escapeHtml(j.model_id)]),
        el("td", { html: statusBadge(j.status) }),
        el("td", {}, [progressBar(0, "progress")]),
        el("td", { class: "dim" }, [fmtTime(j.started_at)]),
        el("td", { class: "dim" }, [fmtDur(dur)]),
      ]));
    }
  } else {
    tbody.append(el("tr", {}, [el("td", { colspan: 7 }, [
      emptyState("📭", "No training jobs yet.",
        `<a class="btn btn-primary" href="#/jobs/new">Create your first training job</a>`),
    ])]));
  }
  recentCard.append(table);

  // refresc periòdic lent (dades auxiliars; SSE és per a events de job)
  setTimeout(() => renderDashboard(main), 15000);
}
