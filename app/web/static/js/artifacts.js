// artifacts.js — llista i descàrrega d'artefactes del job.

import { api, escapeHtml, fmtBytes } from "./api.js";
import { el, clear, emptyState, alertBox } from "./ui.js";

export async function renderArtifacts(main, jobId) {
  clear(main);
  main.append(el("h1", {}, ["Artifacts"]));
  main.append(el("a", { href: `#/jobs/${jobId}`, class: "btn btn-sm" }, ["← Job monitor"]));

  const card = el("div", { class: "card mt" });
  main.append(card);
  let arts;
  try { arts = await api(`/api/jobs/${jobId}/artifacts`); }
  catch (e) { card.append(alertBox("error", "API offline", e.message)); return; }

  if (!arts.length) {
    card.append(emptyState("📦", "No artifacts yet.",
      `<a class="btn" href="#/jobs/${jobId}">Back to job monitor</a>`));
    return;
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
      el("td", { class: "dim mono small" }, [escapeHtml(a.sha256)]),
      el("td", {}, [el("a", { class: "btn btn-sm", href: `/api/artifacts/${jobId}/${a.artifact_id}/download` },
                      ["Download"])]),
    );
    table.querySelector("tbody").append(tr);
  }
  card.append(table);
}
