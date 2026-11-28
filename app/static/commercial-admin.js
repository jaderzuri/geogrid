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
