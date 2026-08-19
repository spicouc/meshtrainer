// datasets.js — pantalla de datasets (llista, add, upload, validate).

import { api, uploadDataset, escapeHtml, fmtBytes } from "./api.js";
import { el, clear, statusBadge, emptyState, toast, alertBox } from "./ui.js";

export async function renderDatasets(main) {
  clear(main);
  main.append(el("h1", {}, ["Datasets"]));

  // ── formulari add/upload ──────────────────────────────────────────────
  const form = el("div", { class: "card mb" }, [
    el("h2", {}, ["Add dataset"]),
    el("div", { class: "form-row" }, [
      el("div", { class: "form-field" }, [
        el("label", { for: "ds-local-path" }, ["Local path (JSONL)"]),
        el("input", { id: "ds-local-path", type: "text",
                      placeholder: "/path/to/train.jsonl" }),
      ]),
      el("div", { class: "form-field" }, [
        el("label", { for: "ds-file" }, ["…or upload JSONL"]),
        el("input", { id: "ds-file", type: "file", accept: ".jsonl,.json,.txt" }),
      ]),
    ]),
    // ── Phase 3 (punt 11): zona de drag & drop ──────────────────────────
    el("div", { id: "ds-drop", class: "drop-zone", role: "button", tabindex: "0",
                "aria-label": "Arrossega un fitxer JSONL aquí o clica Upload" },
      ["Drop a JSONL file here, or click Upload below"]),
    el("div", { class: "form-actions" }, [
      el("button", { id: "ds-add-path", class: "btn" }, ["Add local path"]),
      el("button", { id: "ds-upload", class: "btn btn-primary" }, ["Upload JSONL"]),
    ]),
  ]);
  main.append(form);

  form.querySelector("#ds-add-path").addEventListener("click", async () => {
    const path = form.querySelector("#ds-local-path").value.trim();
    if (!path) return toast("Indica un path local", "error");
    try {
      const ds = await api("/api/datasets", {
        method: "POST",
        body: JSON.stringify({ source_path: path, name: "" }),
      });
      toast(`Dataset ${ds.dataset_id} registrat`, "ok");
      renderDatasets(main);
    } catch (e) {
      toast(e.message, "error");
    }
  });

  form.querySelector("#ds-upload").addEventListener("click", async () => {
    const file = form.querySelector("#ds-file").files[0];
    if (!file) return toast("Selecciona un fitxer JSONL", "error");
    try {
      const ds = await uploadDataset(file);
      toast(`Dataset ${ds.dataset_id} pujat (${fmtBytes(ds.size)})`, "ok");
      renderDatasets(main);
    } catch (e) {
      toast(e.message, "error");
    }
  });

  // ── Phase 3 (punt 11): drag & drop ────────────────────────────────────
  const dropZone = form.querySelector("#ds-drop");
  if (dropZone) {
    const dz = dropZone;
    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", async (e) => {
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!file) return;
      const input = form.querySelector("#ds-file");
      // assigna el fitxer al <input> via DataTransfer (API moderna)
      try {
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
      } catch (err) { /* fallback: si no es pot assignar, es puja directament */ }
      toast(`Arrossegat: ${file.name} (${fmtBytes(file.size)}) — clica Upload`, "ok");
    });
  }

  // ── llista ────────────────────────────────────────────────────────────
  const listCard = el("div", { class: "card" }, [el("h2", {}, ["All datasets"])]);
  main.append(listCard);
  let dss;
  try { dss = await api("/api/datasets"); } catch (e) {
    listCard.append(alertBox("error", "API offline", e.message));
    return;
  }
  if (!dss.length) {
    listCard.append(emptyState("🗂️", "No datasets yet.",
      `<a class="btn" href="#/datasets">Add your first dataset</a>`));
    return;
  }
  const table = el("table", {}, [
    el("thead", {}, [el("tr", {}, [
      "Name", "Format", "Size", "Examples", "Train/Val/Test", "Validation", ""
    ].map((h) => el("th", {}, [h])))]),
  ]);
  const tbody = el("tbody");
  table.append(tbody);
  for (const ds of dss) {
    const row = el("tr", {});
    row.append(
      el("td", {}, [escapeHtml(ds.name)]),
      el("td", { class: "dim" }, [escapeHtml(ds.format)]),
      el("td", { class: "dim mono" }, [fmtBytes(ds.size)]),
      el("td", {}, [String(ds.examples)]),
      el("td", { class: "dim" }, [`${ds.train_count}/${ds.validation_count}/${ds.test_count}`]),
      el("td", { html: statusBadge(ds.validation_status) }),
      el("td", {}, [
        el("button", { class: "btn btn-sm", "data-validate": ds.dataset_id }, ["Validate"]),
      ]),
    );
    tbody.append(row);
  }
  listCard.append(table);

  for (const btn of listCard.querySelectorAll("[data-validate]")) {
    btn.addEventListener("click", async () => {
      try {
        const res = await api(`/api/datasets/${btn.dataset.validate}/validate`,
                              { method: "POST" });
        toast(`Validació: ${res.validation_status}`, res.validation_status === "PASS" ? "ok" : "error");
        if (res.validation_errors && res.validation_errors.length) {
          const errBox = alertBox("error", "Dataset validation failed", "");
          const ul = el("ul");
          for (const e of res.validation_errors.slice(0, 8)) {
            ul.append(el("li", { class: "small" }, [escapeHtml(e)]));
          }
          errBox.append(ul);
          listCard.after(errBox);
        }
        renderDatasets(main);
      } catch (e) {
        toast(e.message, "error");
      }
    });
  }
}
