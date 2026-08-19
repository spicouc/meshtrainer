// monitor.js — Job Monitor: pantalla central del producte.
// Header + cards + worker cards + timeline + metrics + SSE (E0-04).

import { api, connectSSE, escapeHtml, fmtDur, fmtTime, fmtBytes } from "./api.js";
import { el, clear, statusBadge, kpi, toast, alertBox, progressBar, svgEl } from "./ui.js";

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
    statusBadge(job.status),
  ]));
  header.append(el("div", { class: "dim small mt" }, [
    `${escapeHtml(job.backend_id)} · ${escapeHtml(job.model_id)} · started ${fmtTime(job.started_at)} · elapsed ${fmtDur(elapsed)}`,
  ]));

  const progress = job.rounds ? job.rounds_progress ?? 0 : 0;
  header.append(progressBar(progress, "job progress"));
  // ── v1.4.1 (P1 total progress): barra gran + percentatge + stage ─────
  const progCard = el("div", { class: "card mb", id: "monitor-total-progress" });
  const progRow = el("div", { class: "flex" });
  const progPct = el("span", { class: "prog-pct", id: "prog-pct" }, ["0%"]);
  const progStage = el("span", { class: "badge badge-pending", id: "prog-stage" }, ["PREPARING"]);
  const progRound = el("span", { class: "dim small", id: "prog-round" }, ["Round 0/0"]);
  progRow.append(progPct, progRound, progStage);
  const progBar = el("div", { class: "progress-bar progress-lg", role: "progressbar",
                              "aria-valuemin": "0", "aria-valuemax": "100",
                              "aria-valuenow": "0", id: "prog-bar" },
                    [el("div", { class: "progress-fill", id: "prog-fill" })]);
  progCard.append(progRow, progBar);
  main.append(progCard);
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

  // ── SSE (contracte E0-04 + v1.4.1 P1: incremental, sense full refresh) ──
  const sseBadge = el("span", { class: "badge badge-pending", id: "sse-badge" }, ["SSE: connecting…"]);
  header.append(el("div", { class: "flex mt" }, [sseBadge]));
  let es = null;
  let highestSeq = 0;
  let lastFull = 0;
  const seenSeqs = new Set();
  const timers = [];  // v1.4.1 FT-20: timers de throttle netejables
  const onSseStatus = (s) => {
    const b = document.getElementById("sse-badge");
    if (!b) return;
    if (s === "open") { b.textContent = "SSE: live"; b.className = "badge badge-pass"; }
    else { b.textContent = "Reconnecting…"; b.className = "badge badge-pending"; }
  };
  // v1.4.1: cada event actualitza NOMÉS el component afectat (no full render)
  const onEvent = (ev) => {
    const seq = Number(ev.seq || 0);
    if (seq && (seenSeqs.has(seq) || seq <= highestSeq)) return; // dedup
    if (seq) { seenSeqs.add(seq); highestSeq = Math.max(highestSeq, seq); }
    addTimeline(ev);
    const t = ev.type || "";
    if (t === "job.status" || t === "round.create") {
      const pl = ev.payload || {};
      if (pl.status) {
        const badge = header.querySelector(".badge");
        if (badge) badge.replaceWith(statusBadge(pl.status));
      }
      refreshOnce(500);
    } else if (t === "round.fedavg" || t === "contribution.submit" ||
               t === "contribution.activate") {
      refreshOnce(400);            // progress/workers/loss
    } else if (t === "job.progress") {
      refreshOnce(300);
    } else if (t === "artifact.add") {
      refreshArtifactsOnce();
    } else if (t === "worker.spawn" || t === "worker.state") {
      refreshWorkersOnce();
    } else {
      refreshOnce(600);            // qualsevol altre event: refresc lleu
    }
  };
  // throttling: màxim 1 sincronització REST/segon (P1)
  let pending = false;
  function refreshOnce(delay) {
    if (pending) return;
    pending = true;
    const t = setTimeout(() => { pending = false; refresh(jobId); }, delay);
    timers.push(t);
  }
  let artPending = false;
  function refreshArtifactsOnce() {
    if (artPending) return;
    artPending = true;
    const t = setTimeout(() => { artPending = false; renderArtifacts(lastJob || {}); }, 400);
    timers.push(t);
  }
  let wkPending = false;
  function refreshWorkersOnce() {
    if (wkPending) return;
    wkPending = true;
    const t = setTimeout(() => { wkPending = false; refresh(jobId); }, 250);
    timers.push(t);
  }
  // bootstrap REST → last_seq (P1)
  api(`/api/jobs/${jobId}/events?format=json`).then((evs) => {
    for (const ev of evs) {
      addTimeline(ev);
      if (ev.seq) highestSeq = Math.max(highestSeq, Number(ev.seq));
    }
  }).catch(() => {});
  es = connectSSE(jobId, { onEvent, onStatus: onSseStatus, afterSeq: () => highestSeq });
  // v1.4.1 (P1 route cleanup, FT-20): registra neteja explícita —
  // tanca EventSource + cancel·la timers/throttle pendents de la vista
  window.__monitorES = es;
  window.__currentCleanup = () => {
    try { es && es.close(); } catch (e) {}
    window.__monitorES = null;
    for (const t of timers) { try { clearTimeout(t); } catch (e) {} }
    timers.length = 0;
    window.__currentCleanup = null;
  };

  let lastJob = null;
  async function refresh(jid) {
    try {
      const j = await api(`/api/jobs/${jid}`);
      lastJob = j;
      renderHeader(j);
      renderTotalProgress(j);
      renderKpis(j);
      renderWorkers(j);
      renderMetrics(j);
      renderChart(j);
      if (["COMPLETED", "FAILED", "CANCELLED"].includes(j.status)) {
        if (es) { es.close(); }
        renderArtifacts(j);
      }
    } catch (e) { /* API transitori — el polling de SSE el reintenta */ }
  }

  // ── v1.4.1 (P1): progrés total des del contracte de l'API ─────────────
  function renderTotalProgress(j) {
    const pctEl = document.getElementById("prog-pct");
    const stageEl = document.getElementById("prog-stage");
    const roundEl = document.getElementById("prog-round");
    const barEl = document.getElementById("prog-bar");
    const fillEl = document.getElementById("prog-fill");
    if (!pctEl) return;
    const p = j.progress || {};
    const frac = Number(p.overall_fraction || 0);
    const pct = Math.max(0, Math.min(100, Math.round(frac * 100)));
    pctEl.textContent = `${pct}%`;
    if (roundEl) roundEl.textContent = `Round ${p.round_current ?? 0}/${p.round_total ?? 0}`;
    if (stageEl) {
      stageEl.textContent = String(p.stage || "PREPARING");
      const done = p.stage === "COMPLETED";
      stageEl.className = "badge " + (done ? "badge-pass" : "badge-pending");
    }
    if (barEl) barEl.setAttribute("aria-valuenow", String(pct));
    if (fillEl) fillEl.style.width = `${pct}%`;
  }

  function renderHeader(j) {
    const h = header.querySelector("h1");
    if (h) h.textContent = j.name || "";
    const badge = header.querySelector(".badge");
    if (badge) badge.replaceWith(statusBadge(j.status));
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
    const box = workersBox;
    if (!box) return;
    clear(box);
    // v1.4.1 (P0): workers REALS persistits (PID, state, host, shard, round)
    const ws = j.workers_detail || [];
    const ex = j.execution || {};
    box.append(el("div", { class: "dim small mb" }, [
      `Execution: ${escapeHtml(String(ex.location || "local-server"))} · ` +
      `certified workers: ${ex.certified_workers ?? 2}`,
    ]));
    if (!ws.length) {
      box.append(el("div", { class: "dim" }, ["No workers spawned yet…"]));
      return;
    }
    for (const w of ws) {
      const st = String(w.state || "starting").toLowerCase();
      const stCls = st === "done" || st === "exit-0" ? "badge-pass"
        : st === "starting" || st === "running" ? "badge-pending"
        : "badge-fail";
      const card = el("div", { class: "worker-card" }, [
        el("div", { class: "w-head" }, [
          el("span", { class: "w-id" }, [escapeHtml(w.worker_id)]),
          el("span", { class: `badge ${stCls}` }, [escapeHtml(String(w.state))]),
        ]),
        el("div", { class: "w-row" }, [el("span", {}, ["PID"]), el("b", {}, [String(w.pid ?? "—")])]),
        el("div", { class: "w-row" }, [el("span", {}, ["Device"]), el("b", {}, [escapeHtml(String(w.device || "cpu"))])]),
        el("div", { class: "w-row" }, [el("span", {}, ["Shard"]), el("b", {}, [escapeHtml(String(w.shard || "—"))])]),
        el("div", { class: "w-row" }, [el("span", {}, ["Round"]), el("b", {}, [escapeHtml(String(w.round || "—"))])]),
        el("div", { class: "w-row" }, [el("span", {}, ["Host"]), el("b", {}, [escapeHtml(String(w.execution_host || "local-server"))])]),
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
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    });
    // v1.4.1 (P0 XSS): SVG construït amb DOM API — mai innerHTML
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
                               "aria-label": "Loss trend" });
    svg.append(svgEl("polyline", { fill: "none", stroke: "#4da3ff",
                                   "stroke-width": "2", points: pts.join(" ") }));
    svg.append(svgEl("text", { x: String(P), y: String(H - 8), fill: "#93a0b8",
                               "font-size": "10" }, ["round 0"]));
    svg.append(svgEl("text", { x: String(W - P - 40), y: String(H - 8),
                               fill: "#93a0b8", "font-size": "10" },
                     [`round ${losses.length - 1}`]));
    box.append(svg);
  }

  function renderArtifacts(j) {
    const box = document.getElementById("monitor-artifacts");
    if (!box) return;
    api(`/api/jobs/${j.job_id}/artifacts`).then((arts) => {
      clear(box);
      if (!arts.length) {
        box.append(el("div", { class: "dim" }, ["No artifacts yet…"])); return;
      }
      // ── Phase 3 (punt 21): pantalla de resultat per a COMPLETED ────────
      if (j.status === "COMPLETED") {
        const adapters = arts.filter((a) => a.type.startsWith("adapter_"));
        const summary = arts.find((a) => a.type === "training_summary.json");
        const lastAdapter = adapters[adapters.length - 1];
        const doneCard = el("div", { class: "result-card" }, [
          el("h2", { class: "result-title" }, ["✓ Training completed"]),
        ]);
        if (lastAdapter) {
          doneCard.append(
            el("div", { class: "result-adapter" }, [
              el("div", { class: "dim small" }, ["Final adapter"]),
              el("div", { class: "mono" }, [escapeHtml(lastAdapter.type)]),
              el("div", { class: "small dim" },
                [`${fmtBytes(lastAdapter.size)} · SHA-256 ${escapeHtml(lastAdapter.sha256.slice(0, 16))}…`]),
              el("div", { class: "mt" }, [
                el("a", { class: "btn btn-primary",
                          href: `/api/artifacts/${j.job_id}/${lastAdapter.artifact_id}/download` },
                   ["Download adapter"]),
              ]),
            ]),
          );
        }
        if (summary) {
          doneCard.append(el("div", { class: "mt" }, [
            el("a", { class: "btn btn-sm",
                      href: `/api/artifacts/${j.job_id}/${summary.artifact_id}/download` },
               ["Download training summary"]),
          ]));
        }
        box.append(doneCard);
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

  // càrrega inicial d'events + estat (el bootstrap SSE ja omple highestSeq)
  refresh(jobId);
}
