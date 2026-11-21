const RESULT_LABEL = {
  pass: "In range",
  boundary: "On limit",
  fail: "Out of range",
  review: "Check",
  incomplete: "Missed",
  missed: "Missed",
  empty: "Empty",
  na: "-",
};

const state = {
  jobs: [],
  job: null,
  rows: [],
  instruments: [],
  status: null,
  dirty: false,
  uploadPurpose: "report",
  uploadTarget: "current",
  device: null,
  downloadExport: null,
};

const $ = (id) => document.getElementById(id);

function reloadApp() {
  const stamp = Date.now();
  const next = new URL(window.location.href);
  next.searchParams.set("_r", String(stamp));
  next.hash = "";
  window.location.replace(next.pathname + next.search);
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => node.classList.remove("show"), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "include", ...options });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_error) {
      /* keep status text */
    }
    if (response.status === 401 && path !== "/api/login" && path !== "/api/me") showLogin();
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response;
}

function parseNumber(value) {
  if (value == null) return null;
  const text = String(value).trim().replace(/\s/g, "").replace(/,/g, ".").replace("±", "").replace(/[°º˚]/g, "");
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function parseTol(value) {
  if (value == null || String(value).trim() === "") return null;
  const number = parseNumber(String(value).replace("+", "").replace("−", "-").replace("–", "-"));
  return number == null ? null : Math.abs(number);
}

function splitNominal(nominal, tolPlus, tolMinus) {
  let plus = parseTol(tolPlus);
  let minus = parseTol(tolMinus);
  const text = nominal == null ? "" : String(nominal).trim();
  if (plus == null && minus == null && text) {
    const bilateral = text.match(/^([+-]?\d+(?:[.,]\d+)?)\s*(?:±|\+\/-|\+\/?-)\s*(\d+(?:[.,]\d+)?)$/);
    if (bilateral) {
      return [bilateral[1].replace(",", "."), Number(bilateral[2].replace(",", ".")), Number(bilateral[2].replace(",", "."))];
    }
  }
  return [text, plus, minus];
}

function band(row) {
  const [nominalText, plus, minus] = splitNominal(row.nominal, row.tol_plus, row.tol_minus);
  return { nominal: parseNumber(nominalText), plus, minus };
}

function measurementStatus(raw, nominal, plus, minus) {
  const text = raw == null ? "" : String(raw).trim();
  if (["-", "—", "–", "NA", "N/A", "NIL", "/"].includes(text.toUpperCase())) return ["na", null];
  const specified = nominal != null && (plus != null || minus != null);
  const plusValue = plus == null ? 0 : plus;
  const minusValue = minus == null ? 0 : minus;
  if (!text) return specified ? ["missed", "missed"] : ["empty", null];
  const token = text.replace(/\s/g, "").toUpperCase();
  const passWords = new Set(["OK", "PASS", "P", "GOOD", "ACCEPT", "ACCEPTED", "YES"]);
  const failWords = new Set(["NG", "NOK", "FAIL", "F", "REJECT", "REJECTED", "BAD", "NO"]);
  if (passWords.has(token)) return ["pass", null];
  if (failWords.has(token)) return ["fail", `${raw} is a reject entry`];
  const number = parseNumber(raw);
  if (number == null) return ["review", "Could not read this as a dimension"];
  if (!specified) return ["review", "No Dim ± Tol on this row"];
  const lsl = nominal - minusValue;
  const usl = nominal + plusValue;
  if (number < lsl - 1e-9 || number > usl + 1e-9) {
    return number > usl
      ? ["fail", `${trimNum(number)} is above ${trimNum(usl)}`]
      : ["fail", `${trimNum(number)} is below ${trimNum(lsl)}`];
  }
  const onLimit = (plusValue > 0 && Math.abs(number - (nominal + plusValue)) <= 0.0001)
    || (minusValue > 0 && Math.abs(number - (nominal - minusValue)) <= 0.0001);
  if (onLimit) return ["boundary", `${trimNum(number)} is on Dim ± Tol`];
  return ["pass", null];
}

function trimNum(number) {
  return String(Number(number.toFixed(4)));
}

function evaluate(row) {
  const { nominal, plus, minus } = band(row);
  const [m1Status, m1Reason] = measurementStatus(row.m1, nominal, plus, minus);
  const [m2Status, m2Reason] = measurementStatus(row.m2, nominal, plus, minus);
  const reasons = [];
  if (m1Reason) reasons.push(`T/W: ${m1Reason}`);
  if (m2Reason) reasons.push(`F/W: ${m2Reason}`);
  const statuses = [m1Status, m2Status];
  let result = "empty";
  if (statuses.includes("fail")) result = "fail";
  else if (statuses.includes("boundary")) result = "boundary";
  else if (statuses.includes("review")) result = "review";
  else if (statuses.includes("pass") && statuses.includes("missed")) result = "incomplete";
  else if (statuses.includes("pass")) result = "pass";
  else if (statuses.includes("missed")) result = "missed";
  else if (statuses.length && statuses.every((status) => status === "na")) result = "na";
  if (row.ocr_note && ["review", "incomplete", "missed", "empty"].includes(result) && !reasons.includes(row.ocr_note)) {
    reasons.push(row.ocr_note);
  }
  if (result === "pass") reasons.unshift("Inside Dim ± Tol");
  else if (result === "incomplete") {
    const missing = [["T/W", m1Status], ["F/W", m2Status]].filter(([, status]) => status === "missed").map(([label]) => label);
    if (missing.length) reasons.push(`${missing.join(" and ")} missed`);
  } else if (result === "missed" && reasons.length === 0) reasons.push("Actual dimension missed");
  else if (result === "empty" && reasons.length === 0) reasons.push("Empty");
  row.result = result;
  row.m1_status = m1Status;
  row.m2_status = m2Status;
  row.range_warning = false;
  row.reasons = reasons;
  return row;
}

function paintRow(tr, row) {
  evaluate(row);
  tr.className = `result-${row.result}`;
  const result = tr.querySelector(".status");
  result.replaceChildren();
  const pill = document.createElement("span");
  pill.className = "pill-result";
  pill.textContent = RESULT_LABEL[row.result] || row.result;
  const why = document.createElement("span");
  why.className = "why";
  why.textContent = (row.reasons || []).join(" · ");
  result.append(pill, why);
  tr.querySelectorAll("td[data-measure]").forEach((cell) => {
    cell.classList.remove("cell-pass", "cell-boundary", "cell-fail", "cell-missed", "cell-review", "cell-na");
    const status = row[cell.dataset.measure === "m1" ? "m1_status" : "m2_status"];
    if (status && status !== "empty") cell.classList.add(`cell-${status}`);
  });
  updateCounts();
}

function updateCounts() {
  const counts = { pass: 0, fail: 0, review: 0, incomplete: 0, empty: 0, range: 0 };
  state.rows.forEach((row) => {
    evaluate(row);
    counts[row.result] = (counts[row.result] || 0) + 1;
    if (row.range_warning) counts.range += 1;
  });
  const missed = (counts.missed || 0) + (counts.incomplete || 0);
  $("counts").textContent = `${counts.pass || 0} in range · ${counts.boundary || 0} on limit · ${counts.fail || 0} out of range · ${missed} missed`;
}

function visibleRows() {
  const page = $("pageFilter").value;
  const area = $("areaFilter").value;
  return state.rows.filter((row) => {
    if (page !== "all" && String(row.page_no) !== page) return false;
    if (area !== "all" && (row.work_area || "") !== area) return false;
    return true;
  });
}

function field(row, name, className, listId) {
  const td = document.createElement("td");
  td.className = className;
  const input = document.createElement("input");
  input.value = name === "tol" ? formatTol(row) : (row[name] ?? "");
  input.dataset.field = name;
  input.autocomplete = "off";
  if (listId) input.setAttribute("list", listId);
  if (["m1", "m2", "nominal", "tol_plus", "tol_minus"].includes(name)) input.inputMode = "decimal";
  input.addEventListener("input", () => onEdit(row, name, input.value, input.closest("tr")));
  td.append(input);
  if (name === "m1" || name === "m2") td.dataset.measure = name;
  return td;
}

function renderGrid() {
  const body = $("gridBody");
  body.replaceChildren();
  visibleRows().forEach((row) => {
    if (!row._key) row._key = row.id ? `id-${row.id}` : `new-${crypto.randomUUID()}`;
    evaluate(row);
    const tr = document.createElement("tr");
    tr.dataset.key = row._key;
    tr.className = `result-${row.result}`;
    tr.append(
      field(row, "balloon_no", "balloon"),
      field(row, "characteristic", "char"),
      field(row, "nominal", "nominal"),
      field(row, "tol", "tol"),
      field(row, "instrument", "instrument", "instrumentNames"),
      field(row, "m1", "measure"),
      field(row, "m2", "measure"),
      field(row, "sheet_result", "sheet-result"),
    );
    const result = document.createElement("td");
    result.className = "status";
    tr.append(result);
    const actions = document.createElement("td");
    actions.className = "row-actions";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove row";
    remove.addEventListener("click", () => {
      state.rows = state.rows.filter((item) => item !== row);
      state.dirty = true;
      refreshFilters();
      renderGrid();
    });
    actions.append(remove);
    tr.append(actions);
    body.append(tr);
    paintRow(tr, row);
  });
  updateCounts();
}

function formatTol(row) {
  const plus = parseTol(row.tol_plus);
  const minus = parseTol(row.tol_minus);
  if (plus == null && minus == null) return "-";
  if (plus != null && minus != null && Math.abs(plus - minus) < 1e-9) return `±${trimNum(plus)}`;
  return `+${trimNum(plus || 0)}/-${trimNum(minus || 0)}`;
}

function applyTol(row, value) {
  const text = value.trim();
  if (!text || text === "-" || text === "—" || text === "–") {
    row.tol_plus = null;
    row.tol_minus = null;
    return;
  }
  const bilateral = text.match(/^(?:±|\+\/-|\+\/?-)\s*(\d+(?:[.,]\d+)?)/);
  const amount = parseTol(bilateral ? bilateral[1] : text);
  row.tol_plus = amount;
  row.tol_minus = amount;
}

function onEdit(row, name, value, tr) {
  if (name === "tol") applyTol(row, value);
  else row[name] = value;
  if (name === "m1" || name === "m2") {
    row[`${name}_source`] = "manual";
    row[`${name}_confident`] = true;
  }
  state.dirty = true;
  if (name === "page_no" || name === "work_area") refreshFilters(false);
  paintRow(tr, row);
}

function refreshFilters(rebuild = true) {
  const page = $("pageFilter");
  const area = $("areaFilter");
  const pageValue = page.value || "all";
  const areaValue = area.value || "all";
  const pages = [...new Set(state.rows.map((row) => String(row.page_no || 1)))].sort((a, b) => Number(a) - Number(b));
  const areas = [...new Set(state.rows.map((row) => row.work_area || "").filter(Boolean))];
  page.replaceChildren(new Option("All pages", "all"));
  pages.forEach((item) => page.append(new Option(`Page ${item}`, item)));
  area.replaceChildren(new Option("All work areas", "all"));
  areas.forEach((item) => area.append(new Option(item, item)));
  page.value = [...page.options].some((option) => option.value === pageValue) ? pageValue : "all";
  area.value = [...area.options].some((option) => option.value === areaValue) ? areaValue : "all";
  if (rebuild) renderGrid();
}

function fillJobForm() {
  const form = $("jobForm");
  Object.entries(state.job).forEach(([key, value]) => {
    if (form.elements[key]) form.elements[key].value = value || "";
  });
}

function readJobForm() {
  const form = $("jobForm");
  const job = { ...state.job };
  ["job_number", "part_name", "drawing_number", "customer", "report_date", "company_name", "serial_number", "doc_number"].forEach((key) => {
    if (form.elements[key]) job[key] = form.elements[key].value.trim();
  });
  return job;
}

function showWork(hasJob) {
  $("emptyState").hidden = hasJob;
  $("work").hidden = !hasJob;
}

async function loadJobs(selectId) {
  state.jobs = await api("/api/jobs");
  const query = $("jobSearch").value.trim().toLowerCase();
  const list = $("jobList");
  list.replaceChildren();
  state.jobs
    .filter((job) => {
      const haystack = `${job.job_number} ${job.part_number} ${job.part_name}`.toLowerCase();
      return !query || haystack.includes(query);
    })
    .forEach((job) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      if (state.job && state.job.id === job.id) button.classList.add("active");
      const title = document.createElement("strong");
      title.textContent = job.job_number;
      const meta = document.createElement("small");
      meta.textContent = `${job.part_number || job.part_name || "No part"} · ${job.row_count} rows`;
      button.append(title, meta);
      button.addEventListener("click", () => openJob(job.id));
      item.append(button);
      list.append(item);
    });
  if (selectId) await openJob(selectId);
}

async function openJob(id) {
  const payload = await api(`/api/jobs/${id}`);
  state.job = payload.job;
  state.rows = payload.rows;
  state.instruments = payload.instruments;
  state.dirty = false;
  fillLists();
  fillJobForm();
  showWork(true);
  refreshFilters();
  await loadJobs();
}

function fillLists() {
  const names = $("instrumentNames");
  const ids = $("instrumentIds");
  names.replaceChildren();
  ids.replaceChildren();
  const seen = new Set();
  state.instruments.forEach((item) => {
    if (!seen.has(item.name)) {
      seen.add(item.name);
      const option = document.createElement("option");
      option.value = item.name;
      names.append(option);
    }
    if (item.asset_id) {
      const option = document.createElement("option");
      option.value = item.asset_id;
      ids.append(option);
    }
  });
}

async function saveReport() {
  const job = readJobForm();
  const rows = state.rows.map((row, index) => ({ ...row, sort_index: index }));
  const payload = await api(`/api/jobs/${state.job.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job, rows }),
  });
  state.job = payload.job;
  state.rows = payload.rows;
  state.dirty = false;
  fillJobForm();
  refreshFilters();
  await loadJobs();
  toast("Report data saved.");
}

async function checkEntries() {
  const job = readJobForm();
  const payload = await api(`/api/jobs/${state.job.id}/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job, rows: state.rows }),
  });
  const byKey = new Map(payload.rows.map((row, index) => [state.rows[index]._key, row]));
  state.rows.forEach((row) => {
    const checked = byKey.get(row._key);
    if (!checked) return;
    row.result = checked.result;
    row.reasons = checked.reasons;
    row.range_warning = checked.range_warning;
  });
  renderGrid();
  const counts = payload.counts;
  toast(`${counts.pass} correct, ${counts.fail} incorrect, ${counts.review} to check.`);
}

function scopeParams() {
  const scope = $("scope").value;
  const params = new URLSearchParams({ scope });
  if (scope === "page") {
    if ($("pageFilter").value === "all") throw new Error("Choose a page in the filter, then export.");
    params.set("page", $("pageFilter").value);
  }
  if (scope === "area") {
    if ($("areaFilter").value === "all") throw new Error("Choose a work area in the filter, then export.");
    params.set("work_area", $("areaFilter").value);
  }
  return params;
}

function rowsForExportScope(scope, pageValue, areaValue) {
  if (scope === "page" && pageValue !== "all") {
    const page = Number(pageValue);
    return state.rows.filter((row) => Number(row.page_no || 0) === page);
  }
  if (scope === "area" && areaValue !== "all") {
    const target = String(areaValue).trim().toLowerCase();
    return state.rows.filter((row) => String(row.work_area || "").trim().toLowerCase() === target);
  }
  return state.rows;
}

function exportDownloadParams() {
  const scope = $("scope").value;
  const pageValue = $("pageFilter").value;
  const areaValue = $("areaFilter").value;
  try {
    if (scope === "page" && pageValue === "all") throw new Error("Choose a page in the filter, then export.");
    if (scope === "area" && areaValue === "all") throw new Error("Choose a work area in the filter, then export.");
    if (rowsForExportScope(scope, pageValue, areaValue).length) {
      return scopeParams();
    }
    toast("No rows in that page or work area — exporting the full job.");
  } catch (error) {
    if (scope === "job") throw error;
  }
  return new URLSearchParams({ scope: "job" });
}

function downloadNeedsDirectOpen() {
  const ua = navigator.userAgent || "";
  return /Android/i.test(ua) || /iPhone|iPad|iPod/i.test(ua) || /\bwv\b/i.test(ua);
}

function exportFileUrl(path, params) {
  return new URL(`/api/jobs/${state.job.id}/${path}?${params.toString()}`, window.location.href).href;
}

function offerTabletDownload(relativeUrl, fullUrl, filename) {
  const bridge = window.Android || window.IEAndroid || window.SmorxDevice;
  if (bridge && typeof bridge.downloadUrl === "function") {
    bridge.downloadUrl(fullUrl);
    toast("Download started…");
    return;
  }
  state.downloadExport = { path: relativeUrl, filename, fullUrl };
  $("downloadSave").textContent = `Save ${filename}`;
  $("downloadSave").disabled = false;
  $("downloadDialog").showModal();
}

async function saveExportFromTap() {
  const job = state.downloadExport;
  if (!job?.path) return;
  const bridge = window.Android || window.IEAndroid || window.SmorxDevice;
  $("downloadSave").disabled = true;
  try {
    const response = await fetch(job.path, { credentials: "include" });
    if (!response.ok) {
      let message = response.statusText;
      try {
        const body = await response.json();
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch (_error) {
        /* keep status text */
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const type = blob.type || (job.filename.endsWith(".pdf") ? "application/pdf" : "application/octet-stream");
    if (bridge && typeof bridge.saveFile === "function") {
      const base64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
      });
      bridge.saveFile(base64, job.filename, type);
      $("downloadDialog").close();
      toast(`Saved ${job.filename}`);
      return;
    }
    const file = new File([blob], job.filename, { type });
    if (navigator.share && navigator.canShare?.({ files: [file] })) {
      await navigator.share({ files: [file], title: job.filename });
      $("downloadDialog").close();
      toast(`Shared ${job.filename}`);
      return;
    }
    const blobUrl = URL.createObjectURL(blob);
    if (job.filename.endsWith(".pdf")) {
      window.open(blobUrl, "_blank", "noopener");
    }
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = job.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 120000);
    $("downloadDialog").close();
    toast(job.filename.endsWith(".pdf") ? "PDF opened — use Save on screen if needed." : `Saved ${job.filename}`);
  } catch (error) {
    toast(error.message);
    $("downloadDialog").close();
  } finally {
    $("downloadSave").disabled = false;
  }
}

async function download(path, fallback) {
  if (!state.job?.id) throw new Error("Open a job first.");
  if (!state.rows.length) throw new Error("This job has no rows to export.");
  if (state.dirty) await saveReport();
  const params = exportDownloadParams();
  const url = `/api/jobs/${state.job.id}/${path}?${params.toString()}`;

  if (downloadNeedsDirectOpen()) {
    offerTabletDownload(url, exportFileUrl(path, params), fallback);
    return;
  }

  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_error) {
      /* keep status text */
    }
    throw new Error(message);
  }
  const blob = await response.blob();
  const header = response.headers.get("content-disposition") || "";
  const match = header.match(/filename="([^"]+)"/);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = match ? match[1] : fallback;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function openUpload(purpose, target) {
  state.uploadPurpose = purpose;
  state.uploadTarget = target;
  $("uploadTitle").textContent = purpose === "filled" ? "Upload filled report" : "Upload inspection report";
  $("uploadHint").textContent = purpose === "plan"
    ? "Read the printed Sl no, Dim, Tol, and Instrument. Leave Actual (T/W) and Actual (F/W) blank."
    : "Photograph the filled table. Printed Sl numbers usually read. Neat block digits often read. Cursive pen stays blank for you to type.";
  $("uploadFile").value = "";
  $("dropLabel").textContent = "Drop a PDF or photo here, or click to choose";
  $("uploadDialog").showModal();
}

async function submitUpload(event) {
  event.preventDefault();
  const file = $("uploadFile").files[0];
  if (!file) {
    toast("Choose a PDF or image first.");
    return;
  }
  const body = new FormData();
  body.append("file", file);
  body.append("purpose", state.uploadPurpose);
  body.append("replace", $("replaceValues").checked ? "true" : "false");
  const endpoint = state.uploadTarget === "new" || !state.job ? "/api/upload" : `/api/jobs/${state.job.id}/upload`;
  $("uploadSubmit").disabled = true;
  $("uploadSubmit").textContent = "Reading…";
  try {
    const payload = await api(endpoint, { method: "POST", body });
    $("uploadDialog").close();
    state.job = payload.job;
    state.rows = payload.rows;
    state.dirty = false;
    if (payload.instruments) state.instruments = payload.instruments;
    else state.instruments = await api("/api/instruments");
    fillLists();
    fillJobForm();
    showWork(true);
    refreshFilters();
    renderSummary(payload.summary);
    await loadJobs();
    const summary = payload.summary;
    toast(`Filled ${summary.filled_cells || 0} cells. ${(summary.left_blank || []).length} left blank.`);
  } catch (error) {
    toast(error.message);
  } finally {
    $("uploadSubmit").disabled = false;
    $("uploadSubmit").textContent = "Read and fill";
  }
}

function renderSummary(summary) {
  const node = $("ocrSummary");
  if (!summary) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  node.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = "OCR review";
  node.append(title);
  const line = document.createElement("p");
  const counts = summary.counts || {};
  line.textContent = `${summary.pages || 1} page(s). ${summary.matched || 0} rows matched, ${summary.created_rows || 0} added, ${summary.filled_cells || 0} cells filled. ${counts.fail || 0} out of range, ${counts.boundary || 0} on limit, ${(counts.missed || 0) + (counts.incomplete || 0)} missed.`;
  node.append(line);
  const lists = [
    ["Left blank — type these", summary.left_blank],
    ["Incorrect", summary.out_of_tolerance],
    ["Check the reading", summary.needs_review],
    ["Outside instrument range", summary.outside_instrument_range],
    ["Kept what was already typed", summary.conflicts_kept],
    ["Notes", summary.warnings],
  ];
  lists.forEach(([label, items]) => {
    if (!items || !items.length) return;
    const heading = document.createElement("strong");
    heading.textContent = label;
    const list = document.createElement("ul");
    items.slice(0, 12).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      list.append(li);
    });
    node.append(heading, list);
  });
}

function renderPills() {
  const pills = $("pills");
  pills.replaceChildren();
  const status = state.status;
  if (!status) return;
  if (status.database.driver === "postgres") {
    pills.append(makePill(status.database.label || "Lightsail DB", true));
  }
}

function makePill(text, good) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `pill ${good ? "good" : "bad"}`;
  button.textContent = text;
  return button;
}

async function loadStatus() {
  state.status = await api("/api/status");
  renderPills();
  $("settingsForm").elements.anthropic_model.value = state.status.anthropic.model || "claude-sonnet-5-5";
}

function renderGages() {
  const list = $("gageList");
  list.replaceChildren();
  state.instruments.forEach((item) => {
    const row = document.createElement("div");
    row.className = "gage-row";
    const name = document.createElement("span");
    name.textContent = item.name;
    const asset = document.createElement("span");
    asset.textContent = item.asset_id || "—";
    const range = document.createElement("span");
    range.textContent = item.range_min == null ? "any range" : `${item.range_min}–${item.range_max} ${item.unit || ""}`;
    range.style.gridColumn = "span 3";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", async () => {
      await api(`/api/instruments/${item.id}`, { method: "DELETE" });
      state.instruments = await api("/api/instruments");
      fillLists();
      renderGages();
      renderGrid();
    });
    row.append(name, asset, range, remove);
    list.append(row);
  });
}

function addBalloon() {
  const page = $("pageFilter").value === "all" ? 1 : Number($("pageFilter").value);
  const area = $("areaFilter").value === "all" ? "" : $("areaFilter").value;
  state.rows.push({
    id: null,
    page_no: page,
    work_area: area,
    balloon_no: String(state.rows.length + 1),
    characteristic: "",
    nominal: "",
    tol_plus: null,
    tol_minus: null,
    unit: "mm",
    instrument: "",
    instrument_id: "",
    m1: "",
    m2: "",
    m1_confident: true,
    m2_confident: true,
    m1_source: "manual",
    m2_source: "manual",
    sheet_result: "",
    ocr_note: "",
    _key: `new-${crypto.randomUUID()}`,
  });
  state.dirty = true;
  refreshFilters();
}

function wire() {
  $("jobSearch").addEventListener("input", () => loadJobs());
  $("newJobBtn").addEventListener("click", () => $("jobDialog").showModal());
  $("jobCancel").addEventListener("click", () => $("jobDialog").close());
  $("jobDialogForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_number: form.job_number.value.trim(),
          part_number: form.part_number.value.trim(),
          part_name: form.part_name.value.trim(),
          revision: form.revision.value.trim(),
        }),
      });
      form.reset();
      $("jobDialog").close();
      await loadJobs(job.id);
    } catch (error) {
      toast(error.message);
    }
  });
  $("saveBtn").addEventListener("click", () => saveReport().catch((error) => toast(error.message)));
  $("checkBtn").addEventListener("click", () => checkEntries().catch((error) => toast(error.message)));
  $("addRowBtn").addEventListener("click", addBalloon);
  $("pageFilter").addEventListener("change", () => renderGrid());
  $("areaFilter").addEventListener("change", () => renderGrid());
  $("blankBtn").addEventListener("click", () => download("blank.pdf", "report.pdf").catch((error) => toast(error.message)));
  $("xlsxBtn").addEventListener("click", () => download("export.xlsx", "inspection.xlsx").catch((error) => toast(error.message)));
  $("uploadNewBtn").addEventListener("click", () => openUpload("report", "new"));
  $("uploadCancel").addEventListener("click", () => $("uploadDialog").close());
  $("downloadClose").addEventListener("click", () => $("downloadDialog").close());
  $("downloadSave").addEventListener("click", () => saveExportFromTap().catch((error) => toast(error.message)));
  $("uploadForm").addEventListener("submit", submitUpload);
  $("uploadFile").addEventListener("change", () => {
    const file = $("uploadFile").files[0];
    $("dropLabel").textContent = file ? file.name : "Drop a PDF or photo here, or click to choose";
  });
  $("dropZone").addEventListener("dragover", (event) => event.preventDefault());
  $("dropZone").addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer.files[0]) {
      $("uploadFile").files = event.dataTransfer.files;
      $("dropLabel").textContent = event.dataTransfer.files[0].name;
    }
  });
  $("settingsBtn").addEventListener("click", () => $("settingsDialog").showModal());
  $("settingsClose").addEventListener("click", () => $("settingsDialog").close());
  $("settingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anthropic_api_key: form.anthropic_api_key.value.trim(),
        anthropic_model: form.anthropic_model.value.trim(),
      }),
    });
    form.anthropic_api_key.value = "";
    $("settingsDialog").close();
    await loadStatus();
    toast("API key saved on this server.");
  });
  $("gagesBtn").addEventListener("click", async () => {
    state.instruments = await api("/api/instruments");
    fillLists();
    renderGages();
    $("gageDialog").showModal();
  });
  $("gageClose").addEventListener("click", () => $("gageDialog").close());
  $("gageForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api("/api/instruments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name.value.trim(),
          asset_id: form.asset_id.value.trim(),
          range_min: form.range_min.value.trim(),
          range_max: form.range_max.value.trim(),
          unit: form.unit.value.trim(),
        }),
      });
      form.name.value = "";
      form.asset_id.value = "";
      form.range_min.value = "";
      form.range_max.value = "";
      state.instruments = await api("/api/instruments");
      fillLists();
      renderGages();
      renderGrid();
    } catch (error) {
      toast(error.message);
    }
  });
  $("deleteJobBtn").addEventListener("click", async () => {
    if (!state.job) return;
    if (!confirm(`Delete ${state.job.job_number}?`)) return;
    await api(`/api/jobs/${state.job.id}`, { method: "DELETE" });
    state.job = null;
    state.rows = [];
    showWork(false);
    await loadJobs();
  });
  $("jobForm").addEventListener("input", () => {
    state.dirty = true;
  });
  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

function nativeDeviceFromApk() {
  try {
    const bridge = window.Android || window.IEAndroid || window.SmorxDevice;
    if (!bridge) return null;
    if (typeof bridge.getDeviceInfo === "function") {
      const raw = bridge.getDeviceInfo();
      return typeof raw === "string" ? JSON.parse(raw) : raw;
    }
    const serial = typeof bridge.getSerial === "function" ? bridge.getSerial() : "";
    const model = typeof bridge.getModel === "function" ? bridge.getModel() : "";
    if (!serial && !model) return null;
    return {
      device_id: serial || model,
      serial,
      device_name: typeof bridge.getDeviceName === "function" ? bridge.getDeviceName() : "Tablet",
      model,
      platform: "Android",
    };
  } catch (_error) {
    return null;
  }
}

async function detectDevice() {
  try {
    const device = nativeDeviceFromApk() || (await api("/api/device"));
    state.device = device;
    $("deviceLine").textContent = `This device: ${device.device_name} · ${device.device_id}`;
    const check = await api("/api/license/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(device),
    });
    $("licenseNote").textContent = check.message;
    $("loginFields").hidden = check.matched && check.status === "pending";
  } catch (error) {
    $("licenseNote").textContent = error.message;
  }
}

function showLogin() {
  state.user = null;
  $("loginScreen").hidden = false;
  $("app").hidden = true;
  $("loginError").hidden = true;
  detectDevice();
}

function showApp(user) {
  state.user = user;
  $("loginScreen").hidden = true;
  $("app").hidden = false;
  const place = user.client_name ? `${user.client_name} · ` : "";
  $("who").textContent = `${place}${user.role_label}`;
  $("clientsBtn").hidden = user.role !== "super_admin";
  $("settingsBtn").hidden = user.role !== "super_admin";
  $("usersBtn").hidden = user.role !== "client_admin";
}

async function boot() {
  detectDevice();
  try {
    const user = await api("/api/me");
    showApp(user);
  } catch (_error) {
    showLogin();
    return;
  }
  try {
    await loadStatus();
    await loadJobs();
    if (state.user.role === "super_admin") {
      const sample = state.jobs.find((job) => job.job_number === "SAMPLE-2407");
      if (sample) return openJob(sample.id);
    }
    if (state.jobs[0]) return openJob(state.jobs[0].id);
  } catch (error) {
    toast(error.message);
  }
}

function accessRow(title, meta, onRemove) {
  const row = document.createElement("div");
  row.className = "access-row";
  const text = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const small = document.createElement("small");
  small.textContent = meta;
  text.append(strong, document.createElement("br"), small);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Remove";
  remove.addEventListener("click", onRemove);
  row.append(text, remove);
  return row;
}

async function renderClients() {
  const [clients, tablets] = await Promise.all([api("/api/clients"), api("/api/tablets")]);
  const list = $("clientList");
  list.replaceChildren();
  if (!clients.length) {
    list.textContent = "No clients yet.";
    return;
  }
  clients.forEach((client) => {
    const card = document.createElement("section");
    card.className = "client-card";
    const head = document.createElement("div");
    head.className = "client-head access-row";
    const title = document.createElement("strong");
    title.textContent = client.name;
    const removeClient = document.createElement("button");
    removeClient.type = "button";
    removeClient.textContent = "Remove";
    removeClient.addEventListener("click", async () => {
      if (!confirm(`Remove ${client.name} and that client's reports?`)) return;
      await api(`/api/clients/${client.id}`, { method: "DELETE" });
      await renderClients();
    });
    head.append(title, removeClient);
    card.append(head);

    const user = document.createElement("div");
    user.className = "bind-row";
    const userLabel = document.createElement("span");
    userLabel.className = "bind-label";
    userLabel.textContent = "User";
    const userFields = document.createElement("div");
    userFields.className = "bind-fields user-only";
    const userValue = document.createElement("span");
    userValue.textContent = client.admin_username
      ? `${client.admin_username} · ${client.user_count} account${client.user_count === 1 ? "" : "s"}`
      : "No user";
    userFields.append(userValue);
    user.append(userLabel, userFields);
    card.append(user);

    const devices = tablets.filter((item) => item.client_id === client.id);
    devices.forEach((tablet) => {
      const row = document.createElement("div");
      row.className = "bind-row";
      const label = document.createElement("span");
      label.className = "bind-label";
      label.textContent = "Device";
      const fields = document.createElement("div");
      fields.className = "bind-fields";
      const detail = [tablet.name, tablet.model, tablet.device_id.startsWith("MODEL") ? "" : tablet.device_id]
        .filter(Boolean)
        .join(" · ");
      const value = document.createElement("span");
      value.textContent = `${detail} · ${TABLET_STATUS[tablet.status] || tablet.status}`;
      value.style.gridColumn = "1 / span 3";
      const actions = document.createElement("div");
      actions.className = "access-actions";
      if (tablet.status === "pending") {
        const approve = document.createElement("button");
        approve.type = "button";
        approve.className = "solid";
        approve.textContent = "Approve";
        approve.addEventListener("click", async () => {
          await api(`/api/tablets/${tablet.id}/approve`, { method: "POST" });
          await renderClients();
        });
        actions.append(approve);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        await api(`/api/tablets/${tablet.id}`, { method: "DELETE" });
        await renderClients();
      });
      actions.append(remove);
      fields.append(value, actions);
      row.append(label, fields);
      card.append(row);
    });

    const extra = document.createElement("div");
    extra.className = "bind-row";
    const extraLabel = document.createElement("span");
    extraLabel.className = "bind-label";
    extraLabel.textContent = devices.length ? "" : "Device";
    const extraButton = document.createElement("button");
    extraButton.type = "button";
    extraButton.className = "text-action";
    extraButton.textContent = devices.length ? "Add another device for this user" : "Add device";
    extraButton.addEventListener("click", () => {
      extra.replaceWith(deviceFields(client));
    });
    extra.append(extraLabel, extraButton);
    card.append(extra);
    list.append(card);
  });
}

function deviceFields(client) {
  const add = document.createElement("div");
  add.className = "bind-row";
  const addLabel = document.createElement("span");
  addLabel.className = "bind-label";
  addLabel.textContent = "Device";
  const addFields = document.createElement("div");
  addFields.className = "device-add";
  const name = document.createElement("input");
  name.placeholder = `Another device for ${client.name}`;
  const model = document.createElement("input");
  model.placeholder = "Model";
  const serial = document.createElement("input");
  serial.placeholder = "Serial number";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "solid";
  save.textContent = "Add device";
  save.addEventListener("click", async () => {
    try {
      await api("/api/tablets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: client.id,
          name: name.value.trim(),
          model: model.value.trim(),
          device_id: serial.value.trim(),
        }),
      });
      await renderClients();
    } catch (error) {
      toast(error.message);
    }
  });
  addFields.append(name, model, serial, save);
  add.append(addLabel, addFields);
  return add;
}

async function renderUsers() {
  const users = await api("/api/users");
  const list = $("userList");
  list.replaceChildren();
  if (!users.length) {
    list.textContent = "No users yet.";
    return;
  }
  users.forEach((user) => {
    list.append(accessRow(user.display_name || user.username, `${user.username} · ${user.role_label}`, async () => {
      await api(`/api/users/${user.id}`, { method: "DELETE" });
      await renderUsers();
    }));
  });
}

wire();
$("reloadAppBtn").addEventListener("click", reloadApp);
$("reloadAppTopBtn").addEventListener("click", reloadApp);
$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  $("loginError").hidden = true;
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.username.value.trim(),
        password: form.password.value,
        device_id: state.device ? state.device.device_id : "",
        model: state.device ? state.device.model : "",
        serial: state.device ? state.device.serial : "",
      }),
    });
    form.password.value = "";
    await boot();
  } catch (error) {
    $("loginError").hidden = false;
    $("loginError").textContent = error.message;
  }
});
$("signOutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  state.job = null;
  state.rows = [];
  state.jobs = [];
  showLogin();
});
const TABLET_STATUS = {
  registered: "Waiting for the tablet",
  pending: "Needs approval",
  approved: "Approved",
};

$("clientsBtn").addEventListener("click", () => {
  renderClients().then(() => $("clientsDialog").showModal()).catch((error) => toast(error.message));
});
$("clientsClose").addEventListener("click", () => $("clientsDialog").close());
$("clientForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    await api("/api/clients", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name.value.trim(),
        admin_name: form.admin_name.value.trim(),
        admin_username: form.admin_username.value.trim(),
        admin_password: form.admin_password.value,
        device_name: form.device_name.value.trim(),
        device_model: form.device_model.value.trim(),
        device_serial: form.device_serial.value.trim(),
      }),
    });
    form.reset();
    await renderClients();
  } catch (error) {
    toast(error.message);
  }
});
$("usersBtn").addEventListener("click", () => {
  renderUsers().then(() => $("usersDialog").showModal()).catch((error) => toast(error.message));
});
$("usersClose").addEventListener("click", () => $("usersDialog").close());
$("roleSelect").addEventListener("change", () => {
  $("customRole").hidden = $("roleSelect").value !== "other";
});
$("userForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const role = form.role.value === "other" ? form.custom_role.value.trim() : form.role.value;
  try {
    await api("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: form.display_name.value.trim(),
        username: form.username.value.trim(),
        password: form.password.value,
        role,
      }),
    });
    form.reset();
    $("customRole").hidden = true;
    await renderUsers();
  } catch (error) {
    toast(error.message);
  }
});
boot();
