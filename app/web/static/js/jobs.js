// jobs.js — wizard de creació de training job (5 passos) + llista de jobs.

import { api, uploadDataset, escapeHtml, fmtBytes, fmtTime } from "./api.js";
import { el, clear, statusBadge, toast, alertBox, emptyState } from "./ui.js";

const STEPS = ["Model", "Dataset", "Training", "Workers", "Review"];

// formulari del wizard (dades acumulades)
const wiz = {
  step: 1,
  backend_id: "",
  model_id: "",
  dataset_id: "",
  training: { lora_rank: 8, lora_alpha: 16, lora_dropout: 0.05,
              learning_rate: 1e-4, rounds: 1, max_seq_len: 64, seed: 42 },
  workers_cfg: { workers: 2, concurrency: 1, device: "cpu", memory_mb: null },
  preset: "safe",
  job_id: null,
};

export async function renderNewJob(main) {
  clear(main);
  main.append(el("h1", {}, ["New Training Job"]));
  const steps = el("div", { class: "wizard-steps", role: "tablist" });
  STEPS.forEach((s, i) => {
    steps.append(el("span", { class: `wizard-step ${i + 1 === wiz.step ? "active" : ""}`,
                              "data-step": i + 1 }, [`${i + 1}. ${s}`]));
  });
  main.append(steps);
  const body = el("div", { class: "card" });
  main.append(body);
  renderStep(main, body);
}

function setStep(main, body, n) {
  wiz.step = n;
  document.querySelectorAll(".wizard-step").forEach((s) => {
    s.classList.toggle("active", Number(s.dataset.step) === n);
    s.classList.toggle("done", Number(s.dataset.step) < n);
  });
  renderStep(main, body);
}

async function renderStep(main, body) {
  clear(body);
  switch (wiz.step) {
    case 1: return renderStepModel(main, body);
    case 2: return renderStepDataset(main, body);
    case 3: return renderStepTraining(main, body);
    case 4: return renderStepWorkers(main, body);
    case 5: return renderStepReview(main, body);
  }
}

// ── PAS 1: model/backend ────────────────────────────────────────────────
async function renderStepModel(main, body) {
  let bks;
  try { bks = await api("/api/backends"); } catch (e) {
    body.append(alertBox("error", "API offline", e.message)); return;
  }
  body.append(el("h2", {}, ["Select backend & model"]));
  const grid = el("div", { class: "grid grid-2" });
  body.append(grid);

  for (const b of bks) {
    const card = el("div", { class: "card", style: "cursor:pointer",
                             "data-backend": b.id,
                             onclick: async () => {
                               wiz.backend_id = b.id;
                               const res = await api(`/api/models?backend_id=${b.id}`).catch(() => null);
                               const m = res && res.model ? res.model : null;
                               wiz.model_id = m ? m.local_path || m.model_id : "";
                               setStep(main, body, 2);
                             } },
      [
        el("div", { class: "flex" }, [
          el("strong", {}, [escapeHtml(b.display_name || b.id)]),
          b.available
            ? el("span", { class: "badge badge-pass" }, ["available"])
            : el("span", { class: "badge badge-fail" }, ["unavailable"]),
        ]),
        el("div", { class: "dim small mt" }, [escapeHtml(b.model_constraints?.default_model || "")]),
        b.available ? null : el("div", { class: "dim small" }, ["Dependencies missing"]),
      ]);
    grid.append(card);
  }
}

// ── PAS 2: dataset ──────────────────────────────────────────────────────
async function renderStepDataset(main, body) {
  let dss = [];
  try { dss = await api("/api/datasets"); } catch (e) {
    body.append(alertBox("error", "API offline", e.message)); return;
  }
  body.append(el("h2", {}, ["Select dataset"]));

  const select = el("select", { id: "ds-select" });
  select.append(el("option", { value: "" }, ["— tria un dataset —"]));
  for (const ds of dss) {
    const opt = el("option", { value: ds.dataset_id },
      [`${ds.name} (${ds.examples} ex, ${ds.validation_status})`]);
    select.append(opt);
  }
  body.append(el("div", { class: "form-field" }, [
    el("label", { for: "ds-select" }, ["Dataset"]), select,
  ]));

  // upload inline
  body.append(el("div", { class: "form-field" }, [
    el("label", { for: "ds-file" }, ["…or upload JSONL"]),
    el("input", { id: "ds-file", type: "file", accept: ".jsonl,.json,.txt" }),
  ]));

  const info = el("div", { id: "ds-info" });
  body.append(info);

  select.addEventListener("change", async () => {
    const id = select.value;
    if (!id) { clear(info); wiz.dataset_id = ""; return; }
    const ds = dss.find((d) => d.dataset_id === id);
    if (!ds) return;
    wiz.dataset_id = id;
    clear(info);
    info.append(el("div", { class: "flex" }, [
      statusBadge(ds.validation_status),
      el("span", { class: "dim" }, [`${ds.examples} examples · ${fmtBytes(ds.size)}`]),
    ]));
    if (ds.validation_status !== "PASS") {
      info.append(alertBox("warn", "Dataset not validated yet. Validate before continuing.",
        (ds.validation_errors || []).slice(0, 4).join(" · ")));
      const vb = el("button", { class: "btn btn-sm mt" }, ["Validate"]);
      vb.addEventListener("click", async () => {
        try {
          const res = await api(`/api/datasets/${id}/validate`, { method: "POST" });
          toast(`Validació: ${res.validation_status}`, res.validation_status === "PASS" ? "ok" : "error");
          renderStepDataset(main, body);
        } catch (e) { toast(e.message, "error"); }
      });
      info.append(vb);
    }
  });

  const upBtn = el("button", { class: "btn mt" }, ["Upload & select"]);
  upBtn.addEventListener("click", async () => {
    const f = body.querySelector("#ds-file").files[0];
    if (!f) return toast("Selecciona un fitxer", "error");
    try {
      const ds = await uploadDataset(f);
      wiz.dataset_id = ds.dataset_id;
      toast("Dataset pujat — valida'l abans de continuar", "ok");
      renderStepDataset(main, body);
    } catch (e) { toast(e.message, "error"); }
  });
  body.append(upBtn);

  const next = el("button", { class: "btn btn-primary mt", disabled: !wiz.dataset_id }, ["Continue →"]);
  const enableNext = () => { next.disabled = !wiz.dataset_id; };
  next.addEventListener("click", () => setStep(main, body, 3));
  body.append(el("div", { class: "form-actions" }, [
    el("button", { class: "btn", onclick: () => setStep(main, body, 1) }, ["← Back"]),
    next,
  ]));

  // habilita Continue quan es tria/valida un dataset
  const origChange = select.onchange;
  select.addEventListener("change", enableNext);
  body.addEventListener("ds:selected", enableNext);
  enableNext();
}

// ── PAS 3: training config ──────────────────────────────────────────────
function renderStepTraining(main, body) {
  body.append(el("h2", {}, ["Training configuration"]));
  const t = wiz.training;

  const preset = el("div", { class: "form-field" }, [
    el("label", { for: "t-preset" }, ["Preset"]),
    el("select", { id: "t-preset" }, [
      el("option", { value: "safe" }, ["SAFE / DEFAULT"]),
      el("option", { value: "custom" }, ["CUSTOM"]),
    ]),
  ]);
  body.append(preset);

  const custom = el("div", { id: "custom-fields", style: wiz.preset === "custom" ? "" : "display:none" });
  const fields = [
    ["lora_rank", "LoRA rank", "number", 8],
    ["lora_alpha", "LoRA alpha", "number", 16],
    ["lora_dropout", "LoRA dropout", "number", 0.05],
    ["learning_rate", "Learning rate", "number", 1e-4],
    ["rounds", "Rounds", "number", 1],
    ["max_seq_len", "Max sequence length", "number", 64],
    ["seed", "Seed", "number", 42],
  ];
  const row = el("div", { class: "form-row" });
  for (const [key, label, type, def] of fields) {
    const val = t[key] ?? def;
    row.append(el("div", { class: "form-field" }, [
      el("label", { for: `t-${key}` }, [label]),
      el("input", { id: `t-${key}`, type, step: type === "number" ? "any" : undefined,
                    value: String(val),
                    onchange: (e) => {
                      t[key] = type === "number" ? Number(e.target.value) : e.target.value;
                    } }),
    ]));
  }
  custom.append(row);
  body.append(custom);

  preset.querySelector("#t-preset").addEventListener("change", (e) => {
    wiz.preset = e.target.value;
    custom.style.display = e.target.value === "custom" ? "" : "none";
  });

  // resource warning (punt 29)
  if (wiz.backend_id === "minicpm5" && wiz.workers_cfg.workers >= 2) {
    body.append(alertBox("warn",
      "2 MiniCPM5 workers in f32 may require more memory than currently available.",
      "The authoritative decision remains at the application layer."));
  }

  body.append(el("div", { class: "form-actions" }, [
    el("button", { class: "btn", onclick: () => setStep(main, body, 2) }, ["← Back"]),
    el("button", { class: "btn btn-primary", onclick: () => setStep(main, body, 4) }, ["Continue →"]),
  ]));
}

// ── PAS 4: workers ──────────────────────────────────────────────────────
function renderStepWorkers(main, body) {
  body.append(el("h2", {}, ["Workers"]));
  const w = wiz.workers_cfg;
  const row = el("div", { class: "form-row" });
  const f = (key, label, def) => {
    const val = w[key] ?? def;
    row.append(el("div", { class: "form-field" }, [
      el("label", { for: `w-${key}` }, [label]),
      el("input", { id: `w-${key}`, type: "number", value: String(val),
                    onchange: (e) => { w[key] = Number(e.target.value); } }),
    ]));
  };
  f("workers", "Workers (participants de la ronda)", 2);
  f("concurrency", "Concurrency (màxim simultanis)", 1);
  body.append(row);

  body.append(el("div", { class: "alert alert-ok small" }, [
    `Workers: ${w.workers} · Concurrency: ${w.concurrency} — significa ${w.workers} contribucions independents executades com a màxim ${Math.min(w.concurrency, w.workers)} simultàniament.`,
  ]));

  body.append(el("div", { class: "form-actions" }, [
    el("button", { class: "btn", onclick: () => setStep(main, body, 3) }, ["← Back"]),
    el("button", { class: "btn btn-primary", onclick: () => setStep(main, body, 5) }, ["Continue →"]),
  ]));
}

// ── PAS 5: review + validate + start ────────────────────────────────────
async function renderStepReview(main, body) {
  body.append(el("h2", {}, ["Review"]));
  const t = wiz.training;
  const rows = [
    ["Backend", wiz.backend_id], ["Model", wiz.model_id],
    ["Dataset", wiz.dataset_id], ["LoRA rank", String(t.lora_rank)],
    ["LoRA alpha", String(t.lora_alpha)], ["LoRA dropout", String(t.lora_dropout)],
    ["Learning rate", String(t.learning_rate)], ["Rounds", String(t.rounds)],
    ["Max seq len", String(t.max_seq_len)], ["Seed", String(t.seed)],
    ["Workers", String(wiz.workers_cfg.workers)],
    ["Concurrency", String(wiz.workers_cfg.concurrency)],
  ];
  const table = el("table", {}, [el("tbody")]);
  for (const [k, v] of rows) {
    table.querySelector("tbody").append(el("tr", {},
      [el("td", { class: "dim" }, [k]), el("td", {}, [escapeHtml(v)])]));
  }
  body.append(table);

  const statusLine = el("div", { id: "job-status-line", class: "mt" });
  body.append(statusLine);

  const validateBtn = el("button", { class: "btn btn-primary" }, ["VALIDATE JOB"]);
  const startBtn = el("button", { class: "btn", disabled: true }, ["START TRAINING"]);
  startBtn.disabled = true;
  body.append(el("div", { class: "form-actions" }, [
    el("button", { class: "btn", onclick: () => setStep(main, body, 4) }, ["← Back"]),
    validateBtn, startBtn,
  ]));

  validateBtn.addEventListener("click", async () => {
    try {
      let job = wiz.job_id
        ? await api(`/api/jobs/${wiz.job_id}`, {})
        : await api("/api/jobs", { method: "POST", body: JSON.stringify({
            name: `job-${Date.now().toString(36)}`,
            backend_id: wiz.backend_id, model_id: wiz.model_id,
            dataset_id: wiz.dataset_id, training: t, workers_cfg: wiz.workers_cfg,
          }) });
      wiz.job_id = job.job_id;
      job = await api(`/api/jobs/${job.job_id}/validate`, { method: "POST" });
      statusLine.replaceChildren(
        job.validation === "PASS"
          ? el("div", { class: "alert alert-ok" }, ["✓ Validation PASS — ready to start"])
          : alertBox("error", "Validation failed",
                     (job.validation_errors || []).join(" · ")));
      startBtn.disabled = job.validation !== "PASS";
    } catch (e) { toast(e.message, "error"); }
  });

  startBtn.addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${wiz.job_id}/start`, { method: "POST" });
      toast("Training started", "ok");
      location.hash = `#/jobs/${wiz.job_id}`;
    } catch (e) { toast(e.message, "error"); }
  });
}

// ── llista de jobs (per a /jobs) ────────────────────────────────────────
export async function renderJobsList(main) {
  clear(main);
  main.append(el("h1", {}, ["Jobs"]));
  const card = el("div", { class: "card" });
  main.append(card);
  let jobs;
  try { jobs = await api("/api/jobs"); } catch (e) {
    card.append(alertBox("error", "API offline", e.message)); return;
  }
  if (!jobs.length) {
    card.append(emptyState("📭", "No training jobs yet.",
      `<a class="btn btn-primary" href="#/jobs/new">Create your first training job</a>`));
    return;
  }
  const table = el("table", {}, [el("thead", {}, [el("tr", {}, [
    "Name", "Backend", "Status", "Rounds", "Created"
  ].map((h) => el("th", {}, [h])))]), el("tbody")]);
  for (const j of jobs) {
    table.querySelector("tbody").append(el("tr", {}, [
      el("td", {}, [el("a", { href: `#/jobs/${j.job_id}` }, [escapeHtml(j.name)])]),
      el("td", {}, [escapeHtml(j.backend_id)]),
      el("td", { html: statusBadge(j.status) }),
      el("td", { class: "dim" }, [String(j.rounds)]),
      el("td", { class: "dim" }, [fmtTime(j.created_at)]),
    ]));
  }
  card.append(table);
}
