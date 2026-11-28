// GeoGrid UI: business picker, scan launcher, rank heatmap, history, competitor "flip the map".
const $ = (s) => document.querySelector(s);
const KM_PER_MI = 1.609344;
const EARTH_KM = 6371.0088;
const NOT_FOUND = 21;

const helpPanel = $("#helpPanel");
const helpMenuButton = $("#helpMenuBtn");
helpMenuButton.addEventListener("click", () => {
  helpPanel.showModal();
  helpMenuButton.setAttribute("aria-expanded", "true");
  $("#closeHelpPanel").focus();
});
$("#closeHelpPanel").addEventListener("click", () => helpPanel.close());
helpPanel.addEventListener("close", () => {
  helpMenuButton.setAttribute("aria-expanded", "false");
  helpMenuButton.focus();
});
helpPanel.addEventListener("click", (event) => {
  if (event.target === helpPanel) helpPanel.close();
});

const S = {
  businesses: [], business: null, scans: [], scan: null,
  center: null, mode: "rank", competitor: null, pollTimer: null, analysis: null, analysisScanId: null,
};

const DEFAULT_INDICATOR_PREFERENCES = Object.freeze({
  arp: true,
  atrp: true,
  solv: true,
  found: true,
  ivl: true,
  ida: true,
  icm: true,
  icpb_v2: true,
  iop: true,
});
let indicatorPreferences = loadIndicatorPreferences();
let indicatorPreferenceDraft = { ...indicatorPreferences };

function loadIndicatorPreferences() {
  let saved = {};
  try {
    const parsed = JSON.parse(localGet("indicatorPreferences") || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) saved = parsed;
  } catch {
    // Invalid browser storage should leave all indicators enabled.
  }
  return Object.fromEntries(
    Object.keys(DEFAULT_INDICATOR_PREFERENCES).map((id) => [
      id,
      typeof saved[id] === "boolean" ? saved[id] : true,
    ]),
  );
}

function indicatorEnabled(id) {
  return indicatorPreferences[id] !== false;
}

function hasEnabledAnalysisIndicator() {
  return ["ivl", "ida", "icm", "icpb_v2", "iop"].some(indicatorEnabled);
}

function syncIndicatorPreferenceControls() {
  document.querySelectorAll("[data-indicator-toggle]").forEach((input) => {
    const enabled = indicatorPreferenceDraft[input.dataset.indicatorToggle] !== false;
    input.checked = enabled;
    input.closest("[data-indicator-item]")?.classList.toggle("is-disabled", !enabled);
    const state = input.parentElement.querySelector("[data-indicator-state]");
    if (state) state.textContent = enabled ? "Ativo" : "Inativo";
  });
}

function updateIndicatorVisibility() {
  document.querySelectorAll("[data-indicator-column]").forEach((column) => {
    column.classList.toggle("hidden", !indicatorEnabled(column.dataset.indicatorColumn));
  });
  $("#difficultyBtn").classList.toggle("hidden", !indicatorEnabled("ida"));
  $("#opportunityBtn").classList.toggle("hidden", !indicatorEnabled("iop"));
  $("#difficultyLegend").classList.toggle(
    "hidden",
    S.mode !== "difficulty" || !indicatorEnabled("ida"),
  );
  $("#opportunityLegend").classList.toggle(
    "hidden",
    S.mode !== "opportunity" || !indicatorEnabled("iop"),
  );
}

function applyIndicatorPreferences({ restoreDefaults = false } = {}) {
  indicatorPreferences = { ...indicatorPreferenceDraft };
  localSet("indicatorPreferences", JSON.stringify(indicatorPreferences));
  if (
    (S.mode === "difficulty" && !indicatorEnabled("ida"))
    || (S.mode === "opportunity" && !indicatorEnabled("iop"))
  ) {
    S.mode = "rank";
  }
  syncIndicatorPreferenceControls();
  $("#indicatorPreferenceStatus").textContent = restoreDefaults
    ? "Padrão restaurado: todos os indicadores estão ativos."
    : "Preferências aplicadas.";
  if (S.scan) renderScan(false);
  else {
    renderHistoryList();
    updateIndicatorVisibility();
  }
}

syncIndicatorPreferenceControls();
$("#selectAllIndicators").addEventListener("click", () => {
  indicatorPreferenceDraft = { ...DEFAULT_INDICATOR_PREFERENCES };
  syncIndicatorPreferenceControls();
});
$("#deselectAllIndicators").addEventListener("click", () => {
  indicatorPreferenceDraft = Object.fromEntries(
    Object.keys(DEFAULT_INDICATOR_PREFERENCES).map((id) => [id, false]),
  );
  syncIndicatorPreferenceControls();
});
$("#restoreIndicatorDefaults").addEventListener("click", () => {
  indicatorPreferenceDraft = { ...DEFAULT_INDICATOR_PREFERENCES };
  applyIndicatorPreferences({ restoreDefaults: true });
});
$("#applyIndicatorPreferences").addEventListener("click", () => applyIndicatorPreferences());
document.querySelectorAll("[data-indicator-toggle]").forEach((input) => {
  input.addEventListener("change", () => {
    indicatorPreferenceDraft[input.dataset.indicatorToggle] = input.checked;
    syncIndicatorPreferenceControls();
  });
});
updateIndicatorVisibility();

// ---------- map ----------
const map = L.map("map", { zoomControl: false }).setView([39.5, -98.35], 4);
L.control.zoom({ position: "bottomright" }).addTo(map);

// Keep the grid and popups clear of the floating cards.
function cardPadding() {
  const r = (sel) => { const el = $(sel); return el && !el.classList.contains("hidden") && el.offsetParent ? el.getBoundingClientRect() : null; };
  const scan = r("#scanCard"), comp = r("#compCard"), mapBox = $("#map").getBoundingClientRect();
  const top = scan ? scan.bottom - mapBox.top + 20 : 40;
  const right = comp && comp.width < mapBox.width / 2 ? mapBox.right - comp.left + 20 : 40;
  return { topLeft: [40, top], bottomRight: [right, 50] };
}
const maptilerKey = document.querySelector('meta[name="maptiler-api-key"]')?.content.trim();
if (maptilerKey) {
  const tileUrl = `https://api.maptiler.com/maps/streets-v4/{z}/{x}/{y}.png?key=${encodeURIComponent(maptilerKey)}`;
  L.tileLayer(tileUrl, {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.maptiler.com/copyright/">MapTiler</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  }).addTo(map);
} else {
  $("#mapTileStatus").classList.remove("hidden");
}
const gridLayer = L.layerGroup().addTo(map);
let centerMarker = null, bizMarker = null;

// ---------- helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText);
  return body;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, suffix = "") => (v === null || v === undefined ? "–" : `${v}${suffix}`);
const when = (iso) => new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
const gridLabel = (s) => `grade ${s.grid_size}×${s.grid_size} · espaçamento ${+s.spacing_km.toFixed(2)} km`;
function say(sel, kind, text) { const el = $(sel); el.className = `small ${kind}`; el.textContent = text; }

function offset(lat, lng, northKm, eastKm) {
  const dLat = (northKm / EARTH_KM) * 180 / Math.PI;
  const dLng = (eastKm / (EARTH_KM * Math.cos(lat * Math.PI / 180))) * 180 / Math.PI;
  return [lat + dLat, lng + dLng];
}
function gridPoints(lat, lng, size, spacingKm) {
  const half = Math.floor(size / 2), pts = [];
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) pts.push(offset(lat, lng, (half - r) * spacingKm, (c - half) * spacingKm));
  return pts;
}
function spacingKm() {
  const v = parseFloat($("#spacing").value) || 1;
  return $("#unit").value === "mi" ? v * KM_PER_MI : v;
}
function rankClass(rank) {
  if (rank === null || rank === undefined) return "r4";
  return rank <= 3 ? "r1" : rank <= 10 ? "r2" : rank <= 20 ? "r3" : "r4";
}
function sameBiz(r, key) {
  if (key.cid && r.cid) return r.cid === key.cid;
  if (key.place_id && r.place_id) return r.place_id === key.place_id;
  return (r.name || "").toLowerCase() === (key.name || "").toLowerCase();
}
const BRAZIL_DDDS = new Set(
  "11 12 13 14 15 16 17 18 19 21 22 24 27 28 31 32 33 34 35 37 38 41 42 43 44 45 46 47 48 49 51 53 54 55 61 62 63 64 65 66 67 68 69 71 73 74 75 77 79 81 82 83 84 85 86 87 88 89 91 92 93 94 95 96 97 98 99".split(" "),
);
function classifyBrazilPhone(value) {
  if (typeof value !== "string" || !value.trim()) return "Contato indisponível";
  const raw = value.trim();
  const digits = raw.replace(/\D/g, "");
  if (raw.startsWith("+") && !raw.startsWith("+55")) return "Tipo não classificado";
  let local = digits;
  if (digits.startsWith("55") && (digits.length === 12 || digits.length === 13)) {
    local = digits.slice(2);
  }
  if (local.length !== 10 && local.length !== 11) return "Tipo não classificado";
  if (!BRAZIL_DDDS.has(local.slice(0, 2))) return "Tipo não classificado";
  const subscriber = local.slice(2);
  if (subscriber.length === 9 && subscriber.startsWith("9")) return "Celular";
  if (subscriber.length === 8 && /^[2-5]/.test(subscriber)) return "Telefone fixo";
  return "Tipo não classificado";
}
function safePhoneHref(value) {
  const tel = String(value || "").trim().replace(/[^\d+]/g, "");
  return /^\+?\d{7,15}$/.test(tel) ? `tel:${tel}` : null;
}
function safeWebsiteHref(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const candidate = /^https?:\/\//i.test(value) ? value : `https://${value}`;
    const parsed = new URL(candidate);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}
function competitorOccurrences(competitor) {
  return (S.scan.points || []).flatMap((point) => {
    if (point.status !== "ok") return [];
    const result = (point.results || []).find((item) => sameBiz(item, competitor));
    if (!result) return [];
    const position = Number.isSafeInteger(result.position) && result.position > 0
      ? result.position
      : null;
    return [{ row: point.row, col: point.col, position }];
  });
}
function rankIn(results, key) {
  const i = (results || []).findIndex((r) => sameBiz(r, key));
  return i === -1 ? null : i + 1;
}
const myKey = () => ({
  cid: S.scan.business_cid,
  place_id: S.scan.business_place_id,
  name: S.scan.business_name,
});
const targetKey = () => S.competitor || myKey();

// ---------- businesses ----------
async function loadBusinesses(selectId) {
  S.businesses = await api("/api/businesses");
  const sel = $("#bizSelect");
  sel.innerHTML = `<option value="">— add a business below —</option>` +
    S.businesses.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
  const id = selectId ?? localGet("bizId");
  if (id && S.businesses.some((b) => b.id == id)) { sel.value = id; await selectBusiness(+id); }
  else if (S.businesses.length === 1) { sel.value = S.businesses[0].id; await selectBusiness(S.businesses[0].id); }
}

async function selectBusiness(id) {
  closeScan();
  S.business = S.businesses.find((b) => b.id === id) || null;
  $("#runBtn").disabled = !S.business;
  if (!S.business) { $("#bizInfo").textContent = ""; gridLayer.clearLayers(); return; }
  localSet("bizId", id);
  const b = S.business;
  $("#bizInfo").textContent = [b.category, b.address].filter(Boolean).join(" · ");
  if (bizMarker) bizMarker.remove();
  bizMarker = L.marker([b.lat, b.lng], { icon: L.divIcon({ className: "", html: '<div class="biz-pin">📍</div>', iconSize: [26, 26], iconAnchor: [13, 26] }) })
    .bindTooltip(esc(b.name)).addTo(map);
  bizMarker.setZIndexOffset(-1000);  // stay under the rank pins
  setCenter([b.lat, b.lng]);
  await loadHistory();
  if (S.scans.length) await openScan(S.scans[0].id);
  else { drawPreview(); fitGrid(); }
}

$("#bizSelect").addEventListener("change", (e) => selectBusiness(+e.target.value || null));

$("#bizForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#bizQuery").value.trim();
  if (!q) return;
  const btn = e.submitter; btn.disabled = true;
  say("#bizMsg", "muted", "Looking it up on Google Maps…");
  try {
    const b = await api("/api/businesses", { method: "POST", body: JSON.stringify({ query: q }) });
    say("#bizMsg", "ok", `Added: ${b.name}`);
    $("#bizQuery").value = "";
    await loadBusinesses(b.id);
  } catch (err) {
    say("#bizMsg", "err", err.message);
  } finally { btn.disabled = false; }
});

// ---------- grid preview & center ----------
function setCenter(latlng) {
  S.center = latlng;
  if (!centerMarker) {
    centerMarker = L.marker(latlng, { draggable: true, icon: L.divIcon({ className: "", html: '<div class="center-pin"></div>', iconSize: [18, 18] }), zIndexOffset: 2000 })
      .bindTooltip("Grid center: drag to move").addTo(map);
    centerMarker.on("dragend", () => { S.center = [centerMarker.getLatLng().lat, centerMarker.getLatLng().lng]; showPreview(); });
  } else centerMarker.setLatLng(latlng);
}
$("#previewGrid").addEventListener("click", (e) => { e.preventDefault(); if (S.business) { showPreview(); fitGrid(); } });
$("#resetCenter").addEventListener("click", (e) => { e.preventDefault(); if (S.business) { setCenter([S.business.lat, S.business.lng]); showPreview(); } });

function updateHint() {
  const n = +$("#gridSize").value, km = spacingKm(), span = (n - 1) * km;
  const kws = keywords().length || 1;
  const mins = Math.ceil((n * n * kws * 7) / 2 / 60);
  $("#gridHint").textContent = `${n * n} points × ${kws} keyword${kws > 1 ? "s" : ""} · covers ${span.toFixed(1)} × ${span.toFixed(1)} km · ~${mins} min`;
}
function drawPreview() {
  updateHint();
  if (!S.center || S.scan) return;
  if (centerMarker && !map.hasLayer(centerMarker)) centerMarker.addTo(map);
  gridLayer.clearLayers();
  for (const p of gridPoints(S.center[0], S.center[1], +$("#gridSize").value, spacingKm()))
    L.marker(p, { icon: L.divIcon({ className: "", html: '<div class="pin preview"></div>', iconSize: [12, 12] }), interactive: false }).addTo(gridLayer);
}
function showPreview() {  // leave the scan view and show the grid that will be scanned
  if (S.scan && S.scan.status !== "running") closeScan();
  drawPreview();
}
function fitGrid(forPrint = false) {
  if (!S.center) return;
  const [n, km, c] = S.scan
    ? [S.scan.grid_size, S.scan.spacing_km, [S.scan.center_lat, S.scan.center_lng]]
    : [+$("#gridSize").value, spacingKm(), S.center];
  const h = (Math.floor(n / 2) + 0.7) * km;
  const pad = forPrint
    ? { topLeft: [24, 24], bottomRight: [24, 24] }
    : cardPadding();
  map.fitBounds([offset(c[0], c[1], -h, -h), offset(c[0], c[1], h, h)], { paddingTopLeft: pad.topLeft, paddingBottomRight: pad.bottomRight });
}
["#gridSize", "#spacing", "#unit"].forEach((s) => $(s).addEventListener("input", () => { showPreview(); fitGrid(); }));
$("#keywords").addEventListener("input", updateHint);

// ---------- run ----------
function keywords() { return $("#keywords").value.split("\n").map((k) => k.trim()).filter(Boolean); }

$("#runBtn").addEventListener("click", async () => {
  const kws = keywords();
  if (!S.business) return;
  if (!kws.length) { say("#runMsg", "err", "Enter at least one keyword."); return; }
  $("#runBtn").disabled = true; $("#runMsg").textContent = "";
  try {
    const { scan_ids } = await api("/api/scans", { method: "POST", body: JSON.stringify({
      business_id: S.business.id, keywords: kws, grid_size: +$("#gridSize").value,
      spacing_km: +spacingKm().toFixed(4), center_lat: S.center[0], center_lng: S.center[1],
    }) });
    say("#runMsg", "ok", `Started ${scan_ids.length} scan${scan_ids.length > 1 ? "s" : ""}.`);
    await loadHistory();
    await openScan(scan_ids[0]);
  } catch (err) {
    say("#runMsg", "err", err.message);
  } finally { $("#runBtn").disabled = false; }
});

// ---------- history ----------
async function loadHistory() {
  if (!S.business) return;
  S.scans = await api(`/api/scans?business_id=${S.business.id}`);
  renderHistoryList();
}

function renderHistoryList() {
  const ul = $("#history");
  if (!S.scans.length) { ul.innerHTML = '<li class="muted small">No scans yet.</li>'; return; }
  ul.innerHTML = S.scans.map((s) => {
    const m = s.metrics || {};
    const validPoints = m.valid_points ?? m.points ?? 0;
    const coverage = m.coverage_pct ?? (s.total ? Math.round((1000 * validPoints) / s.total) / 10 : 0);
    const hasIncompletePoints = (m.no_results_points || 0) > 0
      || (m.failed_points || 0) > 0
      || (m.pending_points || 0) > 0
      || coverage < 100;
    const active = scanIsActive(s);
    const badge = active
      ? s.status === "running" ? `${s.progress}/${s.total}`
        : s.status === "queued" ? "na fila"
          : s.status === "partial" ? `parcial ${s.progress}/${s.total}`
            : `processando ${s.progress}/${s.total}`
      : s.status === "failed"
        ? "failed"
        : hasIncompletePoints
          ? coverage === 0 ? "sem medição" : `incompleta ${coverage}%`
          : indicatorEnabled("solv") ? `SoLV ${fmt(m.solv, "%")}` : "done";
    return `<li class="item ${S.scan && S.scan.id === s.id ? "active" : ""}" data-id="${s.id}">
      <span class="kw">${esc(s.keyword)}</span><span class="badge ${s.status}">${badge}</span>
      <span class="meta">${when(s.created)} · ${gridLabel(s)} · ${validPoints}/${s.total} válidos (${fmt(coverage, "%")})${["done", "partial"].includes(s.status) && s.finished && indicatorEnabled("arp") ? ` · ARP ${fmt(m.arp)}` : ""}</span></li>`;
  }).join("");
}
$("#history").addEventListener("click", (e) => { const li = e.target.closest("li.item"); if (li) openScan(+li.dataset.id); });

// ---------- scan view ----------
function scanIsActive(scan) {
  return ["running", "queued", "processing"].includes(scan.status)
    || (scan.status === "partial" && !scan.finished);
}
function scanHasFinalAnalysis(scan) {
  return ["done", "partial", "failed"].includes(scan.status) && !scanIsActive(scan);
}
async function openScan(id, { keepView = false } = {}) {
  const scan = await api(`/api/scans/${id}`);
  const switched = !S.scan || S.scan.id !== id;
  S.scan = scan;
  if (switched) { S.analysis = null; S.analysisScanId = null; }
  if (switched && !keepView) { S.competitor = null; S.mode = "rank"; }
  renderScan(switched);
  if (scanHasFinalAnalysis(scan) && (switched || !S.analysis)) loadZuriAnalysis(id);
  document.querySelectorAll("#history li.item").forEach((li) => li.classList.toggle("active", +li.dataset.id === id));
  stopPolling();
  if (scanIsActive(scan)) S.pollTimer = setTimeout(async () => { await loadHistory(); openScan(id, { keepView: true }); }, 2500);
  else if (!switched) loadHistory();
}
function stopPolling() { clearTimeout(S.pollTimer); S.pollTimer = null; }
function closeScan() {
  stopPolling(); S.scan = null; S.competitor = null; S.analysis = null; S.analysisScanId = null;
  $("#scanCard").classList.add("hidden"); $("#compCard").classList.add("hidden");
  document.querySelectorAll("#history li.item").forEach((li) => li.classList.remove("active"));
}

function renderScan(fit) {
  const s = S.scan;
  if (centerMarker) centerMarker.remove();  // it would cover the middle rank pin
  $("#scanCard").classList.remove("hidden");
  $("#scanTitle").textContent = `“${s.keyword}”`;
  $("#scanSub").textContent = `${s.business_name} · ${when(s.created)} · ${gridLabel(s)}`;

  const prog = $("#progress");
  if (scanIsActive(s)) {
    prog.classList.remove("hidden");
    prog.querySelector("div").style.width = `${(100 * s.progress) / s.total}%`;
    prog.querySelector("span").textContent = s.status === "queued" || !s.progress
      ? "Na fila / iniciando…"
      : `${s.status === "partial" ? "Resultados parciais" : "Processando"} ${s.progress} / ${s.total} pontos…`;
  } else prog.classList.add("hidden");

  renderCoverage();
  renderMetrics();
  renderInterpretation();
  const info = $("#analysisInfo");
  if (S.analysis) {
    const ice = S.analysis.ice?.counts || {};
    const analysisDetails = [
      `Base de ARP/ATRP/SoLV/Presença/IVL: ${coverageSummary(s).valid}/${coverageSummary(s).requested} pontos válidos`,
      indicatorEnabled("ida")
        ? `IDA: ${S.analysis.ida_evaluated_points ?? 0}/${coverageSummary(s).valid} pontos válidos avaliados · ${fmt(S.analysis.ida_coverage_pct, "%")} cobertura`
        : null,
      indicatorEnabled("icpb_v2")
        ? `ICP-B: ${S.analysis.icpb_v2?.valid_points ?? 0}/${S.analysis.icpb_v2?.total_points ?? coverageSummary(s).requested} pontos válidos`
        : null,
      indicatorEnabled("icm")
        ? `ICM: ${S.analysis.icm?.observed_occurrences ?? 0} observações da empresa`
        : null,
      `ICE: ${S.analysis.ice?.valid_points ?? 0}/${S.analysis.ice?.total_points ?? coverageSummary(s).requested} pontos válidos`,
      indicatorEnabled("iop") && S.analysis.iop?.point_coverage_pct !== undefined
        ? `IOP: ${S.analysis.iop.point_coverage_pct}% da grade`
        : null,
      `ICE: ${ice.direct ?? 0} diretos, ${ice.indirect ?? 0} indiretos, ${ice.keyword_only ?? 0} por palavra-chave, ${ice.indeterminate ?? 0} indeterminados`,
      indicatorEnabled("iop") ? "IOP: protótipo experimental não validado" : null,
    ].filter(Boolean);
    info.textContent = analysisDetails.join(" · ");
  } else {
    info.textContent = scanHasFinalAnalysis(s) && hasEnabledAnalysisIndicator()
      ? "Carregando análise de indicadores…"
      : "";
  }
  const cmp = s.comparison;
  $("#deltaBtn").disabled = !cmp;
  $("#deltaBtn").title = cmp ? `Comparação com ${when(cmp.previous_created)}` : "Requer pesquisa anterior com a mesma palavra-chave e grade";
  if (!cmp && S.mode === "delta") S.mode = "rank";
  if (S.mode === "difficulty" && (!indicatorEnabled("ida") || !S.analysis)) S.mode = "rank";
  if (S.mode === "opportunity" && (!indicatorEnabled("iop") || S.analysis?.iop?.score == null)) S.mode = "rank";
  $("#difficultyBtn").disabled = !S.analysis || !!S.competitor || !indicatorEnabled("ida");
  $("#opportunityBtn").disabled = S.analysis?.iop?.score == null || !!S.competitor || !indicatorEnabled("iop");
  $("#rankLegend").classList.toggle("hidden", S.mode === "difficulty" || S.mode === "opportunity");
  document.querySelectorAll("#viewToggle button").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
  $("#viewing").innerHTML = S.competitor
    ? `<span class="chip">Exibindo: <b>${esc(S.competitor.name)}</b> <a href="#" id="clearComp">✕ voltar à empresa pesquisada</a></span>` : "";
  const clr = $("#clearComp");
  if (clr) clr.addEventListener("click", (e) => { e.preventDefault(); S.competitor = null; renderScan(false); });

  renderCompetitors();
  updateIndicatorVisibility();
  drawScanGrid();
  if (fit) fitGrid();
}

function renderInterpretation() {
  const panel = $("#interpretationInfo");
  const interpretation = S.analysis?.interpretation;
  const sections = ["coverage", "positioning", "competition"];
  let visible = false;

  sections.forEach((section) => {
    const element = panel.querySelector(`[data-interpretation="${section}"]`);
    const text = typeof interpretation?.[section] === "string"
      ? interpretation[section]
      : "";
    element.textContent = text;
    element.parentElement.hidden = !text;
    visible ||= Boolean(text);
  });
  panel.classList.toggle("hidden", !visible);
}

function coverageSummary(scan) {
  const points = scan.points || [];
  const requested = Number(scan.total ?? scan.grid_size * scan.grid_size);
  const counts = { valid: 0, noResults: 0, failed: 0, pending: 0 };
  for (const point of points) {
    if (point.status === "ok") counts.valid += 1;
    else if (point.status === "no_results") counts.noResults += 1;
    else if (point.status === "pending") counts.pending += 1;
    else counts.failed += 1;
  }
  counts.pending += Math.max(0, requested - points.length);
  const coverage = requested ? Math.round((1000 * counts.valid) / requested) / 10 : 0;
  const level = counts.valid === 0
    ? "sem medição válida"
    : coverage >= 100
      ? "completa"
      : coverage >= 80
        ? "alta cobertura"
        : coverage >= 50
          ? "cobertura moderada"
          : "cobertura insuficiente";
  return { requested, ...counts, coverage, level };
}

function renderCoverage() {
  const summary = coverageSummary(S.scan);
  const execution = scanIsActive(S.scan)
    ? "Em execução"
    : S.scan.status === "failed"
      ? "Execução falhou"
      : S.scan.status === "partial"
        ? "Execução encerrada parcialmente"
      : summary.failed > 0
        ? "Execução encerrada com falhas"
        : summary.pending > 0
          ? "Execução encerrada; há pontos aguardando processamento"
          : summary.noResults > 0
            ? "Execução encerrada; há pesquisas sem resultados"
            : "Execução encerrada";
  $("#coverageInfo").innerHTML =
    `<strong>${execution}</strong> · Cobertura ${summary.level}: ${summary.valid}/${summary.requested} pontos válidos (${summary.coverage}%). ` +
    `Solicitados: ${summary.requested} · válidos: ${summary.valid} · sem resultados: ${summary.noResults} · ` +
    `falhos: ${summary.failed} · pendentes: ${summary.pending}. ` +
    `<span class="muted">Classificação operacional, não representa probabilidade ou confiança estatística.</span>`;
}

function renderMetrics() {
  const s = S.scan;
  let m = s.metrics || {};
  if (S.competitor && s.competitors) m = s.competitors.find((c) => sameBiz(c, S.competitor)) || {};
  const d = (!S.competitor && s.comparison && s.comparison.metrics) || {};
  // For ARP/ATRP lower is better; for SoLV/found higher is better.
  const delta = (k, lowerBetter) => {
    const v = d[k];
    if (v === null || v === undefined || v === 0) return "";
    const good = lowerBetter ? v < 0 : v > 0;
    return `<div class="d ${good ? "up" : "down"}">${v > 0 ? "+" : ""}${v}</div>`;
  };
  if (s.status === "failed") { $("#metrics").innerHTML = `<div class="err small" style="grid-column:1/-1">Pesquisa com falha: ${esc(s.error)}</div>`; return; }
  const metrics = [
    ["arp", "ARP", fmt(m.arp), "posição média quando encontrada", delta("arp", true)],
    ["atrp", "ATRP", fmt(m.atrp), "posição média; 20+ equivale a 21", delta("atrp", true)],
    ["solv", "SoLV", fmt(m.solv, "%"), "pontos entre os três primeiros", delta("solv", false)],
    ["found", "Presença", fmt(m.found_pct, "%"), "pontos entre os 20 primeiros", delta("found_pct", false)],
    ["ivl", "IVL", S.competitor ? "–" : fmt(S.analysis?.metrics?.ivl, "%"), "Indice de visibilidade local (pontos validos)", ""],
    ["ida", "IDA", S.competitor ? "–" : fmt(S.analysis?.ida_average), "Indice experimental de dificuldade (0 a 100)", ""],
    ["icm", "ICM", S.competitor ? "–" : fmt(S.analysis?.icm?.score), "ICM experimental do negócio monitorado, somente se observado", ""],
    ["icpb_v2", "ICP-B V2", S.competitor ? "–" : fmt(S.analysis?.icpb_v2?.score), "Pressão competitiva descritiva; não é probabilidade", ""],
    ["iop", "IOP", S.competitor ? "–" : fmt(S.analysis?.iop?.score), "Protótipo não validado; não é probabilidade nem previsão de vendas", ""],
  ];
  const sample = coverageSummary(s);
  const rankingBasis = `Base: ${sample.valid} de ${sample.requested} pontos válidos.`;
  $("#metrics").innerHTML = metrics
    .filter(([id]) => indicatorEnabled(id))
    .map(([id, label, value, title, change]) =>
      `<div class="metric" data-indicator-card="${id}" title="${esc(title)}${["arp", "atrp", "solv", "found"].includes(id) ? ` ${rankingBasis}` : ""}"><div class="l">${label}</div><div class="v">${value}</div>${change}</div>`,
    ).join("");
}

function renderCompetitors() {
  const s = S.scan, list = s.competitors || [];
  $("#compCard").classList.toggle("hidden", !list.length);
  const me = myKey();
  $("#compTable tbody").innerHTML = list.map((c, i) => {
    const occurrences = competitorOccurrences(c);
    const bestPosition = occurrences
      .filter((entry) => entry.position !== null)
      .sort((a, b) => a.position - b.position || a.row - b.row || a.col - b.col)[0];
    const positionSummary = bestPosition
      ? `Melhor posição original observada: #${bestPosition.position} no ponto L${bestPosition.row + 1}, C${bestPosition.col + 1}`
      : "Posição original não informada pelo provedor";
    const positions = occurrences.length
      ? `<details class="point-positions"><summary>Ver posições por ponto (${occurrences.length})</summary><ul>${occurrences.map((entry) => `<li>Ponto L${entry.row + 1}, C${entry.col + 1}: ${entry.position === null ? "posição original não informada" : `posição original #${entry.position}`}</li>`).join("")}</ul></details>`
      : "";
    const pointResults = (s.points || [])
      .filter((point) => point.status === "ok")
      .flatMap((point) => (point.results || []).filter((item) => sameBiz(item, c)));
    const values = (field) => [...new Set(
      pointResults.map((item) => item[field]).filter((value) => typeof value === "string" && value.trim()),
    )];
    const phones = values("phone");
    const websites = values("url");
    const cids = [...new Set([c.cid, ...values("cid")].filter((value) => typeof value === "string" && value))];
    const placeIds = [...new Set([c.place_id, ...values("place_id")].filter((value) => typeof value === "string" && value))];
    const phoneHtml = phones.length
      ? phones.map((phone) => {
        const href = safePhoneHref(phone);
        const label = `${esc(phone)} <span class="contact-type">(${esc(classifyBrazilPhone(phone))})</span>`;
        return href ? `<a href="${esc(href)}">${label}</a>` : label;
      }).join("<br>")
      : "Indisponível <span class=\"contact-type\">(Contato indisponível)</span>";
    const websiteHtml = websites.length
      ? websites.map((website) => {
        const href = safeWebsiteHref(website);
        return href
          ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(website)}</a>`
          : esc(website);
      }).join("<br>")
      : "Indisponível";
    const identifiersHtml = [
      `CID: ${cids.length ? cids.map(esc).join(", ") : "Indisponível"}`,
      `Place ID: ${placeIds.length ? placeIds.map(esc).join(", ") : "Indisponível"}`,
    ].join("<br>");
    return `
    <tr data-i="${i}" class="${sameBiz(c, me) ? "me" : ""} ${S.competitor && sameBiz(c, S.competitor) ? "sel" : ""}">
      <td>${i + 1}</td>
      <td class="competitor-name" title="${esc(c.name)}${c.category ? " · " + esc(c.category) : ""}">
        <strong>${esc(c.name)}</strong><div class="position-summary">${positionSummary}</div>${positions}
      </td>
      <td class="competitor-contact"><div>Telefone: ${phoneHtml}</div><div>Website: ${websiteHtml}</div></td>
      <td class="competitor-identifiers">${identifiersHtml}</td>
      <td data-indicator-column="solv">${c.solv}%</td>
      <td data-indicator-column="atrp" title="Posição média da empresa nos pontos da grade">ATRP: ${c.atrp}</td>
      <td data-indicator-column="found">${c.found_pct}%</td><td>${fmt(c.rating)}</td>
    </tr>`;
  }).join("");
  updateIndicatorVisibility();
}
$("#compTable tbody").addEventListener("click", (e) => {
  if (e.target.closest("a, details")) return;
  const tr = e.target.closest("tr"); if (!tr) return;
  const c = S.scan.competitors[+tr.dataset.i];
  const isMe = sameBiz(c, myKey());
  S.competitor = isMe || (S.competitor && sameBiz(c, S.competitor)) ? null : { cid: c.cid, name: c.name };
  if (S.competitor) S.mode = "rank";
  renderScan(false);
});
$("#compToggle").addEventListener("click", () => {
  const body = $("#compBody"); body.classList.toggle("hidden");
  $("#compToggle").textContent = body.classList.contains("hidden") ? "+" : "–";
});

function drawScanGrid() {
  const s = S.scan;
  gridLayer.clearLayers();
  const key = targetKey();
  const byCell = new Map(s.points.map((p) => [`${p.row},${p.col}`, p]));
  const pts = gridPoints(s.center_lat, s.center_lng, s.grid_size, s.spacing_km);
  const pad = cardPadding();
  const markerSize = s.grid_size >= 11 ? 24 : 34;
  pts.forEach(([lat, lng], idx) => {
    const row = Math.floor(idx / s.grid_size), col = idx % s.grid_size;
    const p = byCell.get(`${row},${col}`);
    let cls, label, iconStyle = "";
    if (!p || p.status === "pending") { cls = "pending"; label = "…"; }
    else if (p.status === "no_results") { cls = "no-results"; label = "Ø"; }
    else if (p.status !== "ok") { cls = "rx"; label = "!"; }
    else {
      const rank = S.competitor ? rankIn(p.results, key) : p.rank;
      if (S.mode === "difficulty" && S.analysis && !S.competitor) {
        const a = (S.analysis.points || []).find((x) => x.row === row && x.col === col);
        const ida = a?.difficulty?.ida;
        if (ida === null || ida === undefined) { cls = "ida-missing"; label = "?"; }
        else { cls = ida < 35 ? "ida-easy" : ida < 70 ? "ida-medium" : "ida-hard"; label = String(Math.round(ida)); }
      } else if (S.mode === "opportunity" && S.analysis && !S.competitor) {
        const opportunity = (S.analysis.iop?.points || []).find((x) => x.row === row && x.col === col);
        if (opportunity?.score === null || opportunity?.score === undefined) {
          cls = "ida-missing"; label = "?";
        } else {
          cls = "iop"; label = String(Math.round(opportunity.score));
          iconStyle = `--iop-hue:${Math.round(120 * opportunity.score / 100)}`;
        }
      } else if (S.mode === "delta" && s.comparison) {
        const prevRaw = s.comparison.points[`${row},${col}`];
        if (prevRaw === undefined) { cls = "same"; label = "?"; }
        else {
          const diff = (prevRaw ?? NOT_FOUND) - (rank ?? NOT_FOUND);  // positive = moved up
          cls = diff > 0 ? "up" : diff < 0 ? "down" : "same";
          label = diff > 0 ? `+${diff}` : diff < 0 ? `${diff}` : "=";
        }
      } else { cls = rankClass(rank); label = rank ?? "20+"; }
    }
    const markerStyle = [iconStyle, `--pin-size:${markerSize}px`, `--pin-font-size:${markerSize >= 34 ? 13 : 10}px`]
      .filter(Boolean)
      .join(";");
    const m = L.marker([lat, lng], { icon: L.divIcon({ className: "", html: `<div class="pin ${cls}" style="${markerStyle}">${label}</div>`, iconSize: [markerSize, markerSize] }) });
    m.bindPopup(() => popupHtml(p, key), { maxWidth: 320, autoPanPaddingTopLeft: L.point(pad.topLeft), autoPanPaddingBottomRight: L.point(pad.bottomRight) });
    m.addTo(gridLayer);
  });
}

function popupHtml(p, key) {
  if (!p || p.status === "pending") return `<div class="popup"><h4>Aguardando processamento</h4><div class="muted">Este ponto ainda não tem um resultado final.</div></div>`;
  const providerDetail = p.api_status_code == null
    ? ""
    : `<div class="muted">DataForSEO ${esc(p.api_status_code)}: ${esc(p.api_status_message || "")}</div>`;
  if (p.status === "no_results") {
    return `<div class="popup"><h4>Sem resultados de pesquisa (Ø)</h4><div class="muted">A consulta não retornou resultados orgânicos; este ponto não é contado como posição 20+.</div>${providerDetail}</div>`;
  }
  if (p.status !== "ok") {
    const labels = {
      authentication_error: "Falha de autenticação",
      access_denied: "Acesso negado (conta ou IP)",
      insufficient_balance: "Saldo insuficiente",
      rate_limit: "Limite de requisições",
      duplicate_task_limit: "Limite de tarefas duplicadas",
      budget_limit: "Limite de orçamento",
      request_notice: "Aviso da API",
      partial_results: "Resultado parcial",
      task_pending: "Tarefa aguardando processamento",
      task_unavailable: "Tarefa indisponível",
      request_error: "Erro nos parâmetros da consulta",
      technical_error: "Erro técnico",
    };
    const label = labels[p.error_category] || "Falha na consulta";
    return `<div class="popup"><h4>${esc(label)} (!)</h4><div class="muted">${esc(p.error || "A consulta não foi concluída.")}</div>${providerDetail}</div>`;
  }
  const rank = rankIn(p.results, key) ?? (S.competitor ? null : p.rank);
  const a = S.analysis?.points?.find((x) => x.row === p.row && x.col === p.col);
  const ida = a?.difficulty?.ida;
  const difficultyText = !indicatorEnabled("ida")
    ? ""
    : ida == null ? "IDA: dados insuficientes" : `IDA experimental: ${ida} / 100 (${esc(a.difficulty.level)})`;
  const opportunity = S.analysis?.iop?.points?.find((x) => x.row === p.row && x.col === p.col);
  const opportunityText = !indicatorEnabled("iop") || opportunity?.score == null
    ? ""
    : `<div>IOP experimental: ${opportunity.score} / 100</div>`;
  const items = p.results.map((r) => `<li class="${sameBiz(r, key) ? "hit" : ""}">${esc(r.name)}${r.rating ? ` <span class="muted">★${r.rating}</span>` : ""}</li>`).join("");
  const rankText = rank == null ? "20+ (consulta válida; empresa fora do ranking)" : `#${rank}`;
  return `<div class="popup"><h4>${esc(key.name)}: ${rankText}</h4>
    <div class="muted">${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}</div>${difficultyText ? `<div>${difficultyText}</div>` : ""}${opportunityText}<ol>${items || "<li class='muted'>No results</li>"}</ol></div>`;
}

document.querySelectorAll("#viewToggle button").forEach((b) => b.addEventListener("click", () => {
  if (b.disabled) return;
  S.mode = b.dataset.mode; if (S.mode === "delta" || S.mode === "difficulty") S.competitor = null;
  renderScan(false);
}));

window.addEventListener("beforeprint", () => {
  map.invalidateSize({ pan: false });
  fitGrid(true);
  const center = S.scan
    ? [S.scan.center_lat, S.scan.center_lng]
    : S.center;
  if (center) map.panTo(center, { animate: false });
});
window.addEventListener("afterprint", () => {
  map.invalidateSize({ pan: false });
  fitGrid();
});
$("#printBtn").addEventListener("click", () => window.print());
$("#deleteBtn").addEventListener("click", async () => {
  if (!S.scan || !confirm(`Delete scan “${S.scan.keyword}” from ${when(S.scan.created)}?`)) return;
  try { await api(`/api/scans/${S.scan.id}`, { method: "DELETE" }); }
  catch (err) { alert(err.message); return; }
  closeScan(); await loadHistory(); drawPreview();
});

// ---------- per-viewer convenience storage ----------
function localGet(k) { try { return localStorage.getItem("geogrid." + k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem("geogrid." + k, v); } catch { /* storage unavailable */ } }

updateHint();
loadBusinesses().catch((e) => say("#bizMsg", "err", e.message));

// ZURI_PANEL_V1: read-only analysis API, never launches a scan.

function zuriCategoryTerms() {
  const normalize = (value) =>
    String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();

  const keyword = normalize(S.scan?.keyword);
  const businessCategory = S.business?.category || "";

  const groups = [
    {
      terms: [
        "stand", "estande", "cenografia",
        "feira", "exposicao", "exhibition",
        "montadora de stands"
      ],
      categories: [
        "Exhibition",
        "Stand",
        "Event",
        "Trade show",
        "Cenografia",
        "Exhibition stand contractor",
        "Event management company",
        "Trade fair construction company",
        "Scenography company",
        "Stall installation service"
      ]
    },
    {
      terms: [
        "marketing", "publicidade",
        "agencia de propaganda",
        "trafego pago", "seo",
        "marketing digital"
      ],
      categories: [
        "Marketing agency",
        "Advertising agency",
        "Internet marketing service",
        "Agência de marketing",
        "Agência de publicidade"
      ]
    }
  ];

  for (const group of groups) {
    if (group.terms.some(term => keyword.includes(term))) {
      return group.categories;
    }
  }

  return businessCategory ? [businessCategory] : [];
}

async function loadZuriAnalysis(id) {
  try {
    const params = new URLSearchParams();
    zuriCategoryTerms().forEach((term) => params.append("category", term));
    const query = params.toString();
    const analysis = await api(`/api/scans/${id}/analysis${query ? "?" + query : ""}`);
    if (!S.scan || S.scan.id !== id) return;
    S.analysis = analysis;
    S.analysisScanId = id;
    renderScan(false);
  } catch (err) {
    if (!S.scan || S.scan.id !== id) return;
    $("#analysisInfo").textContent = hasEnabledAnalysisIndicator()
      ? `Análise IVL/IDA indisponível: ${err.message}`
      : "";
  }
}
