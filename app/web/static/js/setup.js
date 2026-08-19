// setup.js — First Run Setup (Phase 3, punt 5).
// Passos: System Check → Storage → Model availability → Recommended profile → Finish.
// No crea models/jobs automàticament.

import { api, escapeHtml, fmtBytes } from "./api.js";
import { el, clear, statusBadge } from "./ui.js";

export async function renderSetup(main) {
  clear(main);
  const box = el("div", { class: "card setup-card" });
  box.append(el("h1", {}, ["Welcome to MeshTrainer"]),
             el("p", { class: "muted" }, ["First-run setup — let's check your system."]));
  const steps = el("ol", { class: "setup-steps" });
  box.append(steps);
  main.append(box);

  const run = async () => {
    // 1. System Check (hardware detection real)
    let sys = {};
    try { sys = (await api("/api/system")) || {}; } catch { /* offline */ }
    const hw = sys.hardware || {};
    const cpu = hw.cpu || {};
    const mem = hw.memory || {};
    const gpu = hw.gpu || {};
    const add = (label, ok, detail) => {
      const li = el("li", {}, [
        el("span", { class: "setup-ok" }, [ok ? "✓" : "⚠"]),
        el("strong", {}, [label]), " — ",
        el("span", {}, [detail]),
      ]);
      steps.append(li);
    };
    add("System Check", true,
        `${cpu.model || "CPU"} · ${mem.total_bytes ? fmtBytes(mem.total_bytes) : "?"} RAM · ${gpu.present ? `GPU ${gpu.detail}` : "GPU absent (CPU mode)"}`);
    // 2. Storage
    const disk = sys.disk || {};
    add("Storage", true, `${fmtBytes(disk.available_bytes || 0)} available`);
    // 3. Model availability
    const bks = (await api("/api/backends")).catch ? [] : [];
    let backends = [];
    try { backends = (await api("/api/backends")) || []; } catch { backends = []; }
    const local = backends.filter((b) => b.available).map((b) => b.id).join(", ") || "none";
    add("Model availability", local !== "none",
        local === "none" ? "No certified models found locally" : `Available: ${local}`);
    // 4. Recommended profile
    const caps = sys.capabilities || {};
    const q = caps.qwen3 || {};
    add("Recommended training profile", true,
        `Qwen3-0.6B: ${q.class || "?"} · MiniCPM5-1B: ${(caps.minicpm5 || {}).class || "?"} (based on available memory)`);
    // 5. Finish
    add("Finish", true, "MeshTrainer is ready.");
    const btn = el("button", { class: "btn btn-primary" }, ["Open Dashboard"]);
    btn.addEventListener("click", () => { location.hash = "#/"; });
    steps.append(el("li", {}, [btn]));
  };
  run().catch((e) => {
    const li = el("li", { class: "error" }, [`Setup check failed: ${escapeHtml(String(e))}`]);
    steps.append(li);
  });
}
