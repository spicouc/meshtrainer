// monitor.js — Job Monitor: pantalla central del producte.
// Header + cards + worker cards + timeline + metrics + SSE (E0-04).

import { api, connectSSE, escapeHtml, fmtDur, fmtTime, fmtBytes } from "./api.js";
import { el, clear, statusBadge, kpi, toast, alertBox, progressBar } from "./ui.js";

const EVT_LABELS = {
  "job.status": "Job status → {status}",
  "round.create": "Round {round} created",
  "assignment.create": "Assignment {assignment} (shard {shard}, ETT {expected_ett})",
  "contribution.submit": "Contribution submitted (worker {worker}, ETT {ett})",
  "contribution.validate": "Contribution validated (worker {worker})",
  "contribution.activate": "Contribution activated (worker {worker})",
  "round.fedavg": "FedAvg round {round} → adapter {adapter_sha}",
  "job.progress": "Round {round} of {of}",
  "job.cancel": "Cancellation requested",
  "job.reconcile": "Runner reconciled ({action})",
};

export async function renderMonitor(main, jobId) {
  clear(main);
  let job;
  try { job = await api(`/api/jobs/${jobId}`); }
  catch (e) {
    main.append(alertBox("error", "Job not found or API offline", e.message));
    main.append(el("p", {}, [el("a", { href: "#/" }, ["← Back to dashboard"])]));
    return;
  }

  // ── header ────────────────────────────────────────────────────────────
  const header = el("div", { class: "card mb" });
  const elapsed = job.started_at
    ? (new Date(job.finished_at || Date.now()) - new Date(job.started_at)) / 1000 : null;
  header.append(el("div", { class: "flex" }, [
    el("h1", { style: "margin:0" }, [escapeHtml(job.name)]),
    el("span", { html: statusBadge(job.status) }),
  ]));
  header.append(el("div", { class: "dim small mt" }, [
    `${escapeHtml(job.backend_id)} · ${escapeHtml(job.model_id)} · started ${fmtTime(job.started_at)} · elapsed ${fmtDur(elapsed)}`,
  ]));

  const progress = job.rounds ? job.rounds_progress ?? 0 : 0;
  header.append(progressBar(progress, "job progress"));
  main.append(header);

  // ── controls ──────────────────────────────────────────────────────────
  const controls = el("div", { class: "flex mb" });
  const canCancel = ["RUNNING", "STARTING"].includes(job.status);
  if (canCancel) {
    const cancelBtn = el("button", { class: "btn btn-danger" }, ["Cancel Training"]);
    cancelBtn.addEventListener("click", () => {
      if (!confirm("Cancel training?\nCurrent incomplete work will not be used in FedAvg.")) return;
      api(`/api/jobs/${jobId}/cancel`, { method: "POST" })
        .then(() => { toast("Cancelling…", "ok"); refresh(jobId); })
        .catch((e) => toast(e.message, "error"));
    });
    controls.append(cancelBtn);
  }
  controls.append(el("a", { href: "#/", class: "btn" }, ["← Dashboard"]));
  main.append(controls);

  // ── cards KPIs ────────────────────────────────────────────────────────
  const cards = el("div", { class: "grid grid-4 mb", id: "monitor-kpis" });
  main.append(cards);

  // ── workers + timeline ────────────────────────────────────────────────
  const two = el("div", { class: "grid grid-2 mb" });
  const workersCard = el("div", { class: "card" }, [el("h2", {}, ["Workers"])]);
  const workersBox = el("div", { class: "stack" });
  workersCard.append(workersBox);
  two.append(workersCard);

  const timelineCard = el("div", { class: "card" }, [el("h2", {}, ["Timeline"])]);
  const timelineBox = el("div", { class: "timeline", id: "monitor-timeline" });
  timelineCard.append(timelineBox);
  two.append(timelineCard);
  main.append(two);

  // ── metrics + chart ───────────────────────────────────────────────────
  const metricsCard = el("div", { class: "card mb" }, [el("h2", {}, ["Metrics"])]);
  const metricsBox = el("div", { class: "grid grid-4", id: "monitor-metrics" });
  metricsCard.append(metricsBox);
  main.append(metricsCard);

  const chartCard = el("div", { class: "card mb" }, [el("h2", {}, ["Loss trend"])]);
  const chartBox = el("div", { id: "monitor-chart", class: "chart" });
  chartCard.append(chartBox);
  main.append(chartCard);

  // ── artifacts ─────────────────────────────────────────────────────────
  const artCard = el("div", { class: "card" }, [el("h2", {}, ["Artifacts"])]);
  const artBox = el("div", { id: "monitor-artifacts" });
  artCard.append(artBox);
  main.append(artCard);

  // ── SSE (contracte E0-04) ─────────────────────────────────────────────
  const sseBadge = el("span", { class: "badge badge-pending", id: "sse-badge" }, ["SSE: connecting…"]);
  header.append(el("div", { class: "flex mt" }, [sseBadge]));
  let es = null;
  const onSseStatus = (s) => {
    const b = document.getElementById("sse-badge");
    if (!b) return;
    if (s === "open") { b.textContent = "SSE: live"; b.className = "badge badge-pass"; }
    else { b.textContent = "Reconnecting…"; b.className = "badge badge-pending"; }
  };
  const onEvent = (ev) => {
    addTimeline(ev);
    refresh(jobId); // resincronitza estat via REST (la UI no és font de veritat)
  };
  es = connectSSE(jobId, { onEvent, onStatus: onSseStatus });
  // guarda per netejar si es re-renderitza
  if (window.__monitorES) window.__monitorES.close();
  window.__monitorES = es;

  async function refresh(jid) {
    try {
      const j = await api(`/api/jobs/${jid}`);
      renderHeader(j);
      renderKpis(j);
      renderWorkers(j);
      renderMetrics(j);
      renderArtifacts(j);
      renderChart(j);
      if (["COMPLETED", "FAILED", "CANCELLED"].includes(j.status)) {
        if (es) { es.close(); }
      }
    } catch (e) { /* API transitori — el polling de SSE el reintenta */ }
  }

  function renderHeader(j) {
    const h = header.querySelector("h1");
    if (h) h.textContent = j.name || "";
    const badge = header.querySelector(".badge");
    if (badge) badge.outerHTML = statusBadge(j.status);
    const dim = header.querySelector(".dim.small");
    if (dim) dim.textContent = `${j.backend_id} · ${j.model_id} · started ${fmtTime(j.started_at)}`;
    const pb = header.querySelector(".progress-bar");
    if (pb) {
      const pct = j.rounds ? Math.max(0, Math.min(100, Math.round((j.rounds_progress || 0) * 100))) : 0;
      pb.setAttribute("aria-valuenow", String(pct));
      const fill = pb.querySelector(".progress-fill");
      if (fill) fill.style.width = `${pct}%`;
    }
  }

  function renderKpis(j) {
    const c = document.getElementById("monitor-kpis");
    if (!c) return;
    clear(c);
    const progress = j.rounds ? (j.rounds_progress ?? 0) : 0;
    c.append(
      kpi("Current round", j.rounds_progress ?? "—", `${j.rounds} total`),
      kpi("Workers", j.workers ? `${j.workers}` : "—", "configured"),
      kpi("ETT", j.ett_total ?? "—", ""),
      kpi("Loss", j.last_loss != null ? Number(j.last_loss).toFixed(4) : "—", ""),
    );
  }

  function renderWorkers(j) {
    const box = document.querySelector("#monitor-kpis") && workersBox;
    if (!box) return;
    clear(box);
    // worker info des de logs (si n'hi ha) o placeholder
    const ws = j.workers_cfg_workers ?? j.workers ?? 2;
    for (let i = 0; i < ws; i++) {
      const wid = i === 0 ? "w-A" : `w-${String.fromCharCode(66 + (i - 1))}`;
      const card = el("div", { class: "worker-card" }, [
        el("div", { class: "w-head" }, [
          el("span", { class: "w-id" }, [escapeHtml(wid)]),
          el("span", { class: "badge badge-pending" }, ["IDLE"]),
        ]),
        el("div", { class: "w-row" }, [el("span", {}, ["State"]), el("b", {}, ["IDLE"])]),
        el("div", { class: "w-row" }, [el("span", {}, ["PID"]), el("b", {}, ["—"])]),
        el("div", { class: "w-row" }, [el("span", {}, ["Shard"]), el("b", {}, [i === 0 ? "A" : String.fromCharCode(66 + (i - 1))])]),
      ]);
      box.append(card);
    }
  }

  function renderMetrics(j) {
    const box = document.getElementById("monitor-metrics");
    if (!box) return;
    clear(box);
    box.append(
      kpi("Training loss", j.last_loss != null ? Number(j.last_loss).toFixed(4) : "—"),
      kpi("Validation loss", j.val_loss ?? "—"),
      kpi("Perplexity", j.ppl ?? "—"),
      kpi("Recoveries", j.recoveries ?? 0),
    );
  }

  function renderChart(j) {
    const box = document.getElementById("monitor-chart");
    if (!box) return;
    clear(box);
    const losses = j.loss_history || [];
    if (!losses.length) { box.append(el("div", { class: "dim" }, ["No loss data yet…"])); return; }
    const W = 560, H = 160, P = 30;
    const max = Math.max(...losses.map((l) => l.value)) || 1;
    const min = Math.min(...losses.map((l) => l.value)) || 0;
    const range = (max - min) || 1;
    const pts = losses.map((l, i) => {
      const x = P + (i / Math.max(1, losses.length - 1)) * (W - 2 * P);
      const y = H - P - ((l.value - min) / range) * (H - 2 * P);
      return `${x},${y}`;
    });
    const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Loss trend">
      <polyline fill="none" stroke="#4da3ff" stroke-width="2"
                points="${pts.join(" ")}"/>
      <text x="${P}" y="${H - 8}" fill="#93a0b8" font-size="10">round 0</text>
      <text x="${W - P - 40}" y="${H - 8}" fill="#93a0b8" font-size="10">round ${losses.length - 1}</text>
    </svg>`;
    box.innerHTML = svg;
  }

  function renderArtifacts(j) {
    const box = document.getElementById("monitor-artifacts");
    if (!box) return;
    api(`/api/jobs/${j.job_id}/artifacts`).then((arts) => {
      clear(box);
      if (!arts.length) {
        box.append(el("div", { class: "dim" }, ["No artifacts yet…"])); return;
      }
      const table = el("table", {}, [el("thead", {}, [el("tr", {}, [
        "Type", "Round", "Size", "SHA-256", ""
      ].map((h) => el("th", {}, [h])))]), el("tbody")]);
      for (const a of arts) {
        const tr = el("tr", {});
        tr.append(
          el("td", {}, [escapeHtml(a.type)]),
          el("td", { class: "dim" }, [escapeHtml(a.round || "—")]),
          el("td", { class: "dim mono" }, [fmtBytes(a.size)]),
          el("td", { class: "dim mono small" }, [escapeHtml(a.sha256.slice(0, 16))]),
          el("td", {}, [el("a", { class: "btn btn-sm", href: `/api/artifacts/${j.job_id}/${a.artifact_id}/download` },
                          ["Download"])]),
        );
        table.querySelector("tbody").append(tr);
      }
      box.append(table);
    }).catch(() => {});
  }

  function addTimeline(ev) {
    const box = document.getElementById("monitor-timeline");
    if (!box) return;
    const tpl = EVT_LABELS[ev.type] || ev.type;
    let msg = tpl;
    try {
      const p = ev.payload || {};
      msg = tpl.replace(/\{(\w+)\}/g, (_, k) => p[k] ?? ev[k] ?? "");
    } catch (e) { /* keep raw */ }
    box.append(el("div", { class: "timeline-item" }, [
      el("div", { class: "t-time" }, [fmtTime(ev.ts)]),
      el("div", { class: "t-msg" }, [escapeHtml(msg)]),
    ]));
    box.scrollTop = box.scrollHeight;
  }

  // càrrega inicial d'events existents (format=json: resincronització)
  api(`/api/jobs/${jobId}/events?format=json`).then((evs) => {
    for (const ev of evs) addTimeline(ev);
  }).catch(() => {});
  refresh(jobId);
}
