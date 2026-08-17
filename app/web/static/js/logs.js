// logs.js — visor de logs en viu amb filtres.

import { api, escapeHtml } from "./api.js";
import { el, clear, toast, alertBox } from "./ui.js";

export async function renderLogs(main, jobId) {
  clear(main);
  main.append(el("h1", {}, ["Logs"]));
  main.append(el("a", { href: `#/jobs/${jobId}`, class: "btn btn-sm" }, ["← Job monitor"]));

  const card = el("div", { class: "card mt" });
  main.append(card);

  // filtres
  const row = el("div", { class: "form-row" });
  const workerSel = el("select", { id: "log-worker" });
  workerSel.append(el("option", { value: "" }, ["all workers"]));
  const roundSel = el("select", { id: "log-round" });
  roundSel.append(el("option", { value: "" }, ["all rounds"]));
  row.append(
    el("div", { class: "form-field" }, [el("label", { for: "log-worker" }, ["Worker"]), workerSel]),
    el("div", { class: "form-field" }, [el("label", { for: "log-round" }, ["Round"]), roundSel]),
    el("div", { class: "form-field" }, [
      el("label", {}, ["&nbsp;"]),
      el("button", { id: "log-follow", class: "btn btn-sm" }, ["Follow logs"]),
    ]),
  );
  card.append(row);

  const viewer = el("div", { class: "log-viewer", id: "log-viewer", tabindex: "0",
                             "aria-label": "Log output" });
  card.append(viewer);

  let follow = true;
  let lastFile = "";
  let lastLine = 0;

  async function load() {
    const w = workerSel.value, r = roundSel.value;
    const q = new URLSearchParams();
    if (w) q.set("worker", w);
    if (r) q.set("round", r);
    let logs;
    try { logs = await api(`/api/jobs/${jobId}/logs?${q}`); }
    catch (e) { viewer.append(alertBox("error", "Logs no disponibles", e.message)); return; }
    clear(viewer);
    for (const l of logs) {
      const isErr = /error|fail|traceback|exception/i.test(l.line);
      viewer.append(el("div", { class: `log-line${isErr ? " l-err" : ""}` }, [
        el("span", { class: "l-file" }, [`[${escapeHtml(l.file)}] `]),
        escapeHtml(l.line),
      ]));
    }
    if (follow) viewer.scrollTop = viewer.scrollHeight;
  }

  card.querySelector("#log-follow").addEventListener("click", () => {
    follow = !follow;
    card.querySelector("#log-follow").textContent = follow ? "Follow logs (on)" : "Follow logs (off)";
  });
  workerSel.addEventListener("change", load);
  roundSel.addEventListener("change", load);

  await load();
  // refresc lent (dades auxiliars; no substitut de SSE)
  const iv = setInterval(() => { if (follow) load(); }, 4000);
  main.dataset.cleanup = () => clearInterval(iv);
}
