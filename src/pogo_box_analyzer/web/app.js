let latestCsvBlob = null;
let latestCsvName = "species_counts.csv";

const fileInput = document.getElementById("screenshots-input");
const fileCountEl = document.getElementById("file-count");
const analyzeBtn = document.getElementById("analyze-btn");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const summaryEl = document.getElementById("summary");
const previewTable = document.getElementById("preview-table");
const downloadBtn = document.getElementById("download-btn");
const shareBtn = document.getElementById("share-btn");

init();

function init() {
  fileInput.addEventListener("change", () => {
    const n = fileInput.files ? fileInput.files.length : 0;
    fileCountEl.textContent = `${n} file${n === 1 ? "" : "s"} selected.`;
  });

  analyzeBtn.addEventListener("click", analyze);
  downloadBtn.addEventListener("click", downloadCsv);
  shareBtn.addEventListener("click", shareCsv);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // non-fatal
      });
    });
  }
}

async function analyze() {
  const uploads = [];
  const files = fileInput.files || [];

  for (const file of files) {
    uploads.push({
      pass_name: "auto",
      filename: file.name,
      data_base64: await readFileAsBase64(file),
    });
  }

  if (uploads.length === 0) {
    setStatus("Add screenshots before analyzing.");
    return;
  }

  analyzeBtn.disabled = true;
  setStatus(`Encoding and analyzing ${uploads.length} screenshots...`);

  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uploads }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      throw new Error(data.error || "Analyze request failed.");
    }

    const csvText = data.csv_text || "";
    latestCsvBlob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
    latestCsvName = buildCsvFilename();

    renderSummary(data.summary || {});
    renderPreview(data.preview || []);

    resultsEl.classList.remove("hidden");
    setStatus("Done. Download or share the CSV.");
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  } finally {
    analyzeBtn.disabled = false;
  }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const idx = result.indexOf(",");
      if (idx < 0) {
        reject(new Error(`Failed to encode file: ${file.name}`));
        return;
      }
      resolve(result.slice(idx + 1));
    };
    reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function renderSummary(summary) {
  const out = {
    screenshots: summary.screenshots,
    observations_used: summary.observations_used,
    unknown_slots: summary.unknown_slots,
    duplicates_skipped: summary.duplicates_skipped,
    ocr_slots_used: summary.ocr_slots_used,
    auto_pass_screenshots: summary.auto_pass_screenshots,
    auto_pass_detected: summary.auto_pass_detected,
    auto_pass_fallback_all: summary.auto_pass_fallback_all,
  };
  summaryEl.textContent = JSON.stringify(out, null, 2);
}

function renderPreview(rows) {
  previewTable.innerHTML = "";
  if (!rows.length) {
    return;
  }

  const keys = Object.keys(rows[0]);

  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const k of keys) {
    const th = document.createElement("th");
    th.textContent = k;
    hr.appendChild(th);
  }
  thead.appendChild(hr);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const k of keys) {
      const td = document.createElement("td");
      td.textContent = row[k] ?? "";
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }

  previewTable.appendChild(thead);
  previewTable.appendChild(tbody);
}

function downloadCsv() {
  if (!latestCsvBlob) {
    setStatus("No CSV to download yet.");
    return;
  }

  const url = URL.createObjectURL(latestCsvBlob);
  const a = document.createElement("a");
  a.href = url;
  a.download = latestCsvName;
  a.click();
  URL.revokeObjectURL(url);
}

async function shareCsv() {
  if (!latestCsvBlob) {
    setStatus("No CSV to share yet.");
    return;
  }

  const file = new File([latestCsvBlob], latestCsvName, { type: "text/csv" });

  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({
        files: [file],
        title: "PoGo Box Analyzer CSV",
        text: "Pokemon GO species trait counts",
      });
      setStatus("CSV shared.");
      return;
    } catch {
      // fall through to download
    }
  }

  downloadCsv();
}

function setStatus(msg) {
  statusEl.textContent = msg;
}

function buildCsvFilename() {
  const now = new Date();
  const iso = now.toISOString().slice(0, 19).replace(/[:T]/g, "-");
  return `pogo_species_counts_${iso}.csv`;
}
