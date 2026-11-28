const loginPanel = document.getElementById("login-panel");
const adminPanel = document.getElementById("admin-panel");
const loginForm = document.getElementById("login-form");
const logoutButton = document.getElementById("logout");
const message = document.getElementById("message");
const notice = document.getElementById("notice");
const queryInput = document.getElementById("query");
const tableHead = document.getElementById("table-head");
const tableBody = document.getElementById("table-body");
const exportLink = document.getElementById("export");
const consentFilter = document.getElementById("consent-filter");
const qualificationFilter = document.getElementById("qualification-filter");
const providerFilter = document.getElementById("provider-filter");

let activeKind = "leads";

function showMessage(text, isError = false) {
  message.textContent = text;
  message.dataset.error = String(isError);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || response.statusText));
  }
  return payload;
}

function setAuthenticated(operatorId) {
  loginPanel.hidden = Boolean(operatorId);
  adminPanel.hidden = !operatorId;
  logoutButton.hidden = !operatorId;
  showMessage(operatorId ? `Sessão ativa: ${operatorId}` : "");
  if (operatorId) loadRecords();
}

function makeCell(value) {
  const cell = document.createElement("td");
  cell.textContent = value === null || value === undefined ? "—" : String(value);
  return cell;
}

function makeContactCell(value, kind) {
  const cell = document.createElement("td");
  if (typeof value !== "string" || !value.trim()) {
    cell.textContent = "Indisponível";
    return cell;
  }
  let href = null;
  if (kind === "phone") {
    const digits = value.replace(/\D/g, "");
    if (digits.length >= 7 && digits.length <= 15) href = `tel:${digits}`;
  } else {
    try {
      const candidate = /^https?:\/\//i.test(value) ? value : `https://${value}`;
      const parsed = new URL(candidate);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        href = parsed.href;
      }
    } catch {
      href = null;
    }
  }
  if (!href) {
    cell.textContent = value;
    return cell;
  }
  const link = document.createElement("a");
  link.href = href;
  link.textContent = value;
  if (kind === "website") {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  cell.append(link);
  return cell;
}

function renderTable(items) {
  const columns = activeKind === "leads"
    ? [
      ["Empresa", "business_name"],
      ["Cidade", "city"],
      ["Estado", "state"],
      ["Contato", "contact_name"],
      ["E-mail", "email"],
      ["WhatsApp", "whatsapp"],
      ["Consentimento", "marketing_consent"],
      ["Criado em", "created_at"],
    ]
    : [
      ["Empresa candidata", "name"],
      ["Telefone", "phone"],
      ["Website", "website"],
      ["CID", "cid"],
      ["Place ID", "place_id"],
      ["Endereço", "address"],
      ["Categoria", "category"],
      ["Fonte", "source_provider"],
      ["Observações", "observation_count"],
      ["Melhor ordem de resultado em um ponto", "best_rank"],
      ["Posições originais por ponto (JSON)", "provider_positions_by_point"],
      ["Cadastro", "record_status"],
      ["Qualificação", "qualification"],
      ["Exportável", "export_eligible"],
      ["Última ocorrência", "last_seen_at"],
    ];
  tableHead.replaceChildren();
  const headerRow = document.createElement("tr");
  for (const [label] of columns) {
    const heading = document.createElement("th");
    heading.scope = "col";
    heading.textContent = label;
    headerRow.append(heading);
  }
  tableHead.append(headerRow);
  tableBody.replaceChildren();
  for (const item of items) {
    const row = document.createElement("tr");
    for (const [, key] of columns) {
      let value = item[key];
      if (key === "marketing_consent" || key === "export_eligible") {
        value = value ? "Sim" : "Não";
      }
      row.append(
        key === "phone" || key === "website"
          ? makeContactCell(value, key)
          : makeCell(value),
      );
    }
    tableBody.append(row);
  }
  notice.textContent = `${items.length} registro(s) exibido(s).`;
}

async function loadRecords() {
  notice.textContent = "Carregando…";
  const params = new URLSearchParams({ q: queryInput.value.trim(), limit: "200" });
  let endpoint = "/api/commercial/admin/leads";
  if (activeKind === "leads") {
    const consent = document.getElementById("marketing-consent").value;
    if (consent) params.set("marketing_consent", consent);
  } else {
    endpoint = "/api/commercial/admin/prospects";
    const qualification = document.getElementById("qualification").value;
    const provider = document.getElementById("provider").value.trim();
    if (qualification) params.set("qualification", qualification);
    if (provider) params.set("provider", provider);
  }
  try {
    const result = await requestJson(`${endpoint}?${params}`);
    renderTable(result.items);
    if (activeKind === "prospects" && !result.prospecting_export_enabled) {
      notice.textContent += " Exportação bloqueada até aprovação de uso da fonte.";
    }
    showMessage(`Sessão ativa: ${result.operator_id}`);
  } catch (error) {
    notice.textContent = "";
    showMessage(error.message, true);
    if (error.message.includes("session")) setAuthenticated("");
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const operatorId = document.getElementById("operator-id").value.trim();
  const operatorToken = document.getElementById("operator-token").value;
  try {
    const result = await requestJson("/api/commercial/admin/login", {
      method: "POST",
      body: JSON.stringify({ operator_id: operatorId, operator_token: operatorToken }),
    });
    document.getElementById("operator-token").value = "";
    setAuthenticated(result.operator_id);
  } catch (error) {
    showMessage(error.message, true);
  }
});

logoutButton.addEventListener("click", async () => {
  try {
    await requestJson("/api/commercial/admin/logout", { method: "POST", body: "{}" });
    setAuthenticated("");
  } catch (error) {
    showMessage(error.message, true);
  }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    activeKind = tab.dataset.kind;
    document.querySelectorAll(".tab").forEach((item) => {
      item.classList.toggle("active", item === tab);
    });
    consentFilter.hidden = activeKind !== "leads";
    qualificationFilter.hidden = activeKind !== "prospects";
    providerFilter.hidden = activeKind !== "prospects";
    exportLink.href = `/api/commercial/admin/export/${activeKind}.csv`;
    loadRecords();
  });
});

document.getElementById("search").addEventListener("click", loadRecords);
queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadRecords();
});

requestJson("/api/commercial/admin/session")
  .then((result) => setAuthenticated(result.operator_id))
  .catch(() => setAuthenticated(""));

const financePanel = document.getElementById("finance-panel");
const financeStatus = document.getElementById("finance-status");
const financeBalances = document.getElementById("finance-balances");

async function loadFinance() {
  financeBalances.replaceChildren();
  financeStatus.textContent = "Carregando saldos…";
  try {
    const data = await requestJson("/api/commercial/admin/finance");
    if (adminPanel.hidden || !financePanel.open) return;
    const amount = (value) => value == null ? "indisponível" : new Intl.NumberFormat("pt-BR", {
      style: "currency", currency: "USD", maximumFractionDigits: 6,
    }).format(value);
    const rows = [
      ["Limite de tarefas Standard pendentes", data.control.max_pending_tasks ?? "indisponível"],
      ["Lotes Live ativos", data.live_scans.active],
      ["Lotes Live aguardando reconciliação", data.live_scans.held],
      ["Consultas Live autorizadas", data.live_queries.authorized],
      ["Consultas Live incertas", data.live_queries.uncertain],
    ];
    for (const [key, name] of [["daily", "Dia UTC"], ["manual_live", "Evento manual Live"]]) {
      const pool = data.balances[key];
      for (const [field, label] of [
        ["limit_usd", "limite"], ["available_usd", "disponível"],
        ["reserved_usd", "reservado"], ["spent_usd", "consumido"],
      ]) rows.push([`${name} · ${label}`, amount(pool[field])]);
    }
    for (const [label, value] of rows) {
      const title = document.createElement("dt");
      const definition = document.createElement("dd");
      title.textContent = label;
      definition.textContent = value;
      financeBalances.append(title, definition);
    }
    financeStatus.textContent = data.control.enabled ? "Ledger habilitado" : "Ledger desabilitado";
  } catch (error) {
    financeStatus.textContent = error.message;
  }
}

financePanel.addEventListener("toggle", () => { if (financePanel.open) loadFinance(); });
document.getElementById("finance-refresh").addEventListener("click", loadFinance);
logoutButton.addEventListener("click", () => {
  financePanel.open = false;
  financeBalances.replaceChildren();
  financeStatus.textContent = "";
});

const standardForm = document.getElementById("standard-batch");
const standardMode = document.getElementById("standard-modality");
const standardCsv = document.getElementById("standard-csv");
const standardStatus = document.getElementById("standard-status");
const standardProgress = document.getElementById("standard-progress");
const standardConfirm = document.getElementById("standard-confirm");
const standardRefresh = document.getElementById("standard-refresh");
const standardPath = "/api/commercial/admin/standard-scans";
const standardStorage = "geogrid.standard-batch";
let proposedStandard;

function resetStandardQuote() {
  proposedStandard = undefined;
  standardConfirm.disabled = true;
}

standardForm.closest("details").addEventListener("toggle", async (event) => {
  if (!event.currentTarget.open) return;
  try {
    const companies = await requestJson("/api/businesses");
    const items = companies.map((company) => {
      const item = document.createElement("li");
      item.textContent = `${company.id} · ${company.name}`;
      return item;
    });
    document.getElementById("standard-companies").replaceChildren(...items);
    standardRefresh.disabled = !sessionStorage.getItem(standardStorage);
  } catch (error) {
    standardStatus.textContent = error.message;
  }
});

standardMode.addEventListener("change", async () => {
  resetStandardQuote();
  const isLive = standardMode.value === "live";
  document.getElementById("standard-fields").hidden = isLive;
  document.getElementById("manual-live-link").hidden = !isLive;
  if (!isLive) {
    standardStatus.textContent = "";
    return;
  }
  standardStatus.textContent = "Consultando a tarifa Live…";
  try {
    const data = await requestJson("/api/commercial/admin/finance");
    if (standardMode.value !== "live") return;
    const price = data.control.estimated_live_task_cost_usd;
    standardStatus.textContent = price == null
      ? "Tarifa Live indisponível."
      : `Reserva Live estimada: US$ ${price} por ponto. O painel Live mantém autorização e orçamento próprios.`;
  } catch (error) {
    if (standardMode.value === "live") standardStatus.textContent = error.message;
  }
});
standardCsv.addEventListener("input", resetStandardQuote);
document.getElementById("standard-csv-file").addEventListener("change", async (event) => {
  resetStandardQuote();
  const file = event.target.files[0];
  if (!file || file.size > 24000) {
    standardStatus.textContent = "Selecione um arquivo CSV de até 24 KB.";
    return;
  }
  standardCsv.value = await file.text();
});

standardForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetStandardQuote();
  const payload = { modality: standardMode.value, csv_text: standardCsv.value };
  try {
    const quote = await requestJson(`${standardPath}/quote`, {
      method: "POST", body: JSON.stringify(payload),
    });
    if (payload.modality !== standardMode.value || payload.csv_text !== standardCsv.value) return;
    const cost = quote.total_usd == null ? "indisponível" : `US$ ${quote.total_usd}`;
    standardStatus.textContent = `${quote.researches} pesquisas · ${quote.queries} consultas · estimativa ${cost}.`;
    if (quote.enabled) {
      proposedStandard = { ...payload, approved_total_usd: quote.total_usd };
      standardConfirm.disabled = false;
    } else {
      standardStatus.textContent += " Modalidade indisponível no momento.";
    }
  } catch (error) {
    standardStatus.textContent = error.message;
  }
});

async function refreshStandard() {
  try {
    const pending = JSON.parse(sessionStorage.getItem(standardStorage) || "null");
    if (!pending?.batch_id) return;
    const result = await requestJson(`${standardPath}/${pending.batch_id}`);
    const rows = result.researches.map((research) => {
      const item = document.createElement("li");
      item.textContent = `${research.company_name} · ${research.keyword} · ${research.status} · ${research.completed_points}/${research.total_points}`;
      return item;
    });
    const completed = new Map();
    for (const research of result.researches) {
      const group = completed.get(research.manual_business_id) || [];
      group.push(research);
      completed.set(research.manual_business_id, group);
    }
    for (const [business, group] of completed) {
      if (group.length < 3 || group.some((research) => research.status !== "done")) continue;
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = `${standardPath}/${pending.batch_id}/companies/${business}/pdf`;
      link.textContent = `PDF consolidado · ${group[0].company_name}`;
      item.append(link);
      rows.push(item);
    }
    standardProgress.replaceChildren(...rows);
    standardStatus.textContent = `Lote ${result.batch_id}`;
  } catch (error) {
    standardStatus.textContent = error.message;
  }
}

standardConfirm.addEventListener("click", async () => {
  if (!proposedStandard) return;
  const body = JSON.stringify(proposedStandard);
  standardConfirm.disabled = true;
  try {
    const previous = JSON.parse(sessionStorage.getItem(standardStorage) || "null");
    if (previous && !previous.batch_id && previous.body !== body) {
      throw new Error("Existe um envio pendente. Reenvie o mesmo lote para recuperar o identificador.");
    }
    const key = previous?.body === body ? previous.key : crypto.randomUUID();
    sessionStorage.setItem(standardStorage, JSON.stringify({ body, key, batch_id: previous?.body === body ? previous.batch_id : null }));
    const result = await requestJson(standardPath, {
      method: "POST", body, headers: { "Idempotency-Key": key },
    });
    sessionStorage.setItem(standardStorage, JSON.stringify({ body, key, batch_id: result.batch_id }));
    standardRefresh.disabled = false;
    await refreshStandard();
    resetStandardQuote();
  } catch (error) {
    standardStatus.textContent = error.message;
    standardConfirm.disabled = !proposedStandard;
  }
});
standardRefresh.addEventListener("click", refreshStandard);
