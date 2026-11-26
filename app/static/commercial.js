(() => {
  const searchForm = document.getElementById("search-form");
  if (!searchForm) return;

  const byId = id => document.getElementById(id);
  const searchError = byId("search-error");
  const leadError = byId("lead-error");
  const candidateSection = byId("candidate-section");
  const candidateList = byId("candidate-list");
  const leadSection = byId("lead-section");
  const leadForm = byId("lead-form");
  const progressSection = byId("progress-section");
  const reportSection = byId("report-section");
  const stateKey = "zuri-commercial-capability";
  let config = null;
  let privacyConfig = null;
  let search = null;
  let selectedConfirmation = null;
  let leadType = "confirmed";
  let leadSubmission = null;
  let diagnosticSubmission = null;
  let leadState = null;
  let products = {};

  const bytesToHex = bytes =>
    Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");

  const identity = () => {
    let saved;
    try {
      saved = JSON.parse(sessionStorage.getItem(stateKey) || "null");
    } catch {
      saved = null;
    }
    if (!saved || !saved.request_id || !saved.status_token) {
      const bytes = new Uint8Array(32);
      crypto.getRandomValues(bytes);
      saved = { request_id: crypto.randomUUID(), status_token: bytesToHex(bytes) };
      sessionStorage.setItem(stateKey, JSON.stringify(saved));
    }
    return saved;
  };

  const showError = (element, value) => {
    const detail = value && typeof value === "object"
      ? value.message || value.category || "Não foi possível concluir a solicitação."
      : String(value || "Não foi possível concluir a solicitação.");
    element.textContent = detail;
  };

  const jsonRequest = async (url, body) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(
        typeof result.detail === "string"
          ? result.detail
          : result.detail?.message || result.detail?.category || "Falha na solicitação."
      );
      error.status = response.status;
      throw error;
    }
    return result;
  };

  const makeSearch = () => {
    const data = new FormData(searchForm);
    return {
      request_id: crypto.randomUUID(),
      business_name: String(data.get("business_name") || "").trim(),
      city: String(data.get("city") || "").trim(),
      state: String(data.get("state") || "").trim(),
      address: String(data.get("address") || "").trim() || null,
      google_maps_url: String(data.get("google_maps_url") || "").trim() || null
    };
  };

  const productCard = product => {
    const card = document.createElement("article");
    card.className = "product-card";
    const title = document.createElement("h3");
    title.textContent = product.name;
    const detail = document.createElement("p");
    detail.textContent = product.id === "monitor"
      ? `R$ ${product.price_brl}/mês · contratação assistida, sem recorrência automática`
      : `${product.grid_size}×${product.grid_size} · ${product.keywords} palavra-chave · R$ ${product.price_brl}`;
    const availability = document.createElement("p");
    availability.textContent = product.available
      ? "Disponível apenas em homologação simulada"
      : product.assisted_request_enabled
        ? "Disponível para solicitação de atendimento assistido, sem checkout ou processamento automático"
      : product.id === "express" && config?.real_express_enabled
        ? "Disponível via fila n8n Standard Normal, após as aprovações configuradas"
        : "Indisponível até a configuração e aprovação necessárias";
    card.append(title, detail, availability);
    return card;
  };

  const renderCatalog = () => {
    const catalog = byId("product-catalog");
    catalog.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = "Produtos experimentais";
    catalog.append(heading);
    const grid = document.createElement("div");
    grid.className = "product-grid";
    for (const product of Object.values(products)) grid.append(productCard(product));
    catalog.append(grid);
  };

  const candidateCard = (candidate, index) => {
    const card = document.createElement("article");
    card.className = "candidate-card";
    const title = document.createElement("h4");
    title.textContent = candidate.name || "Nome não informado";
    const details = document.createElement("p");
    details.textContent = [
      candidate.address,
      [candidate.city, candidate.state].filter(Boolean).join(" - "),
      candidate.category
    ].filter(Boolean).join(" · ");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "submit-button";
    button.textContent = "Esta é minha empresa";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const confirmed = await jsonRequest("/api/business-finder/confirm", {
          request_id: crypto.randomUUID(),
          candidate_token: candidate.candidate_token,
          confirmed: true
        });
        selectedConfirmation = confirmed.confirmation_token;
        leadType = "confirmed";
        openLeadForm(candidate);
      } catch (error) {
        showError(searchError, error.message);
        button.disabled = false;
      }
    });
    card.append(title, details, button);
    return card;
  };

  const openLeadForm = candidate => {
    candidateSection.hidden = true;
    leadSection.hidden = false;
    byId("lead-eyebrow").textContent = "ETAPA 2 · DADOS E AUTORIZAÇÃO";
    byId("lead-title").textContent = "Prepare seu diagnóstico Express";
    byId("selected-business").textContent = [
      candidate.name,
      candidate.address,
      [candidate.city, candidate.state].filter(Boolean).join(" - ")
    ].filter(Boolean).join(" · ");
    const businessName = leadForm.elements.business_name;
    if (businessName) businessName.value = candidate.name || search.business_name;
    leadForm.elements.keyword.required = true;
    byId("keyword-field").hidden = false;
    leadType = "confirmed";
    leadSection.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const openAssistedForm = () => {
    selectedConfirmation = null;
    leadType = "assisted";
    candidateSection.hidden = true;
    leadSection.hidden = false;
    byId("lead-eyebrow").textContent = "ATENDIMENTO ASSISTIDO";
    byId("lead-title").textContent = "Revise os dados ou solicite ajuda";
    byId("selected-business").textContent =
      "A ausência de candidatos não comprova que a empresa não tenha Perfil da Empresa no Google.";
    leadForm.elements.business_name.value = search.business_name;
    leadForm.elements.keyword.required = false;
    leadForm.elements.keyword.value = "";
    byId("keyword-field").hidden = true;
    byId("lead-button").textContent = "Solicitar atendimento assistido";
    leadSection.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const reportElement = (label, value) => {
    const paragraph = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = `${label}: `;
    paragraph.append(strong, document.createTextNode(
      value === null || value === undefined ? "Dados insuficientes" : String(value)
    ));
    return paragraph;
  };

  const appendContactLink = target => {
    if (!config?.contact_url) return;
    const link = document.createElement("a");
    link.href = config.contact_url;
    link.rel = "noopener noreferrer";
    link.target = "_blank";
    link.textContent = "Falar com a equipe comercial";
    target.append(document.createTextNode(" "), link);
  };

  const renderReport = report => {
    const content = byId("report-content");
    content.replaceChildren();
    content.append(
      reportElement("Empresa", report.business_name),
      reportElement("Palavra-chave", report.keyword),
      reportElement("Grade", `${report.grid_size}×${report.grid_size}`),
      reportElement(
        "Modo de dados",
        report.data_mode === "standard_normal"
          ? "DataForSEO Standard Normal"
          : report.data_mode === "synthetic"
            ? "Simulação com dados fictícios"
            : report.data_mode
      ),
      reportElement("Cobertura válida", `${report.coverage.coverage_pct}% (${report.coverage.valid_points}/${report.coverage.requested_points})`),
      reportElement("ARP", report.metrics.arp),
      reportElement("Visibilidade encontrada", `${report.metrics.found_pct}%`)
    );
    if (report.incomplete) {
      const warning = document.createElement("p");
      warning.className = "message error";
      warning.textContent = "Relatório incompleto: a cobertura válida foi inferior a 100%. Interprete os indicadores com cautela.";
      content.append(warning);
    }
    if (report.contact_action) {
      const contact = document.createElement("p");
      appendContactLink(contact);
      content.append(contact);
    }
    byId("report-title").textContent = report.report_type === "professional"
      ? "Relatório Professional"
      : "Relatório Express";
    byId("upgrade-actions").hidden = report.report_type !== "express";
    reportSection.hidden = false;
    reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const loadReport = async diagnostic => {
    const report = await jsonRequest(
      `/api/commercial/diagnostics/${encodeURIComponent(diagnostic.diagnostic_id)}/report`,
      { lead_id: leadState.lead_id, status_token: leadState.status_token }
    );
    renderReport(report);
  };

  const wait = milliseconds =>
    new Promise(resolve => window.setTimeout(resolve, milliseconds));

  const refreshStatus = async () => {
    if (!leadState) return null;
    progressSection.hidden = false;
    const status = await jsonRequest(
      `/api/commercial/leads/${encodeURIComponent(leadState.lead_id)}/status`,
      { lead_id: leadState.lead_id, status_token: leadState.status_token }
    );
    leadType = status.lead_type || "confirmed";
    const diagnostics = status.diagnostics || [];
    if (!diagnostics.length) {
      byId("progress-title").textContent = leadType === "assisted"
        ? "Solicitação de atendimento recebida"
        : "Nenhum diagnóstico foi iniciado";
      byId("progress-message").textContent = leadType === "assisted"
        ? "A equipe poderá revisar os dados informados. Esta busca não determina se a empresa possui Perfil da Empresa no Google."
        : "A confirmação da empresa é necessária antes de iniciar um diagnóstico.";
      if (leadType === "assisted") appendContactLink(byId("progress-message"));
      return leadType === "assisted" ? "assisted" : "missing";
    }
    const latest = diagnostics[diagnostics.length - 1];
    byId("progress-title").textContent = latest.status === "done"
      ? "Diagnóstico concluído"
      : latest.status === "failed"
        ? "Diagnóstico interrompido"
        : "Diagnóstico em preparação";
    byId("progress-message").textContent =
      `${latest.product_id === "express" ? "Express" : "Professional"} · ${latest.progress}/${latest.total} pontos processados.`;
    if (latest.status === "done") {
      await loadReport(latest);
      progressSection.hidden = true;
    } else if (latest.status === "failed") {
      byId("progress-message").textContent += " Entre em contato para solicitar análise; não faremos uma nova submissão automática.";
    }
    return latest.status;
  };

  const pollStatus = async () => {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const status = await refreshStatus();
      if (["done", "failed", "assisted", "missing"].includes(status)) return;
      await wait(1000);
    }
  };

  const createOrder = async productId => {
    if (!leadState || !leadState.source_diagnostic_id) {
      showError(byId("order-message"), "Conclua o diagnóstico Express antes de solicitar esta opção.");
      return;
    }
    const button = productId === "professional"
      ? byId("professional-button")
      : byId("monitor-button");
    button.disabled = true;
    try {
      const order = await jsonRequest("/api/commercial/orders", {
        request_id: crypto.randomUUID(),
        lead_id: leadState.lead_id,
        status_token: leadState.status_token,
        source_diagnostic_id: leadState.source_diagnostic_id,
        product_id: productId
      });
      const message = byId("order-message");
      message.textContent = order.status === "assisted"
        ? "Solicitação registrada para atendimento assistido. Nenhum pagamento, assinatura ou diagnóstico Professional foi liberado."
        : `Pedido ${order.order_id} criado por R$ ${order.amount_brl}. O checkout não comprova pagamento; a liberação depende de confirmação manual autorizada.`;
      if (order.checkout_url) {
        const link = document.createElement("a");
        link.href = order.checkout_url;
        link.rel = "noopener noreferrer";
        link.target = "_blank";
        link.textContent = "Abrir checkout de teste";
        message.append(document.createTextNode(" "), link);
      }
      button.textContent = "Solicitação registrada";
    } catch (error) {
      showError(byId("order-message"), error.message);
      button.disabled = false;
    }
  };

  const restoreCapability = async () => {
    let saved;
    try {
      saved = JSON.parse(sessionStorage.getItem(stateKey) || "null");
    } catch {
      saved = null;
    }
    if (!saved || !saved.lead_id || !saved.status_token) return;
    leadState = {
      lead_id: saved.lead_id,
      status_token: saved.status_token,
      source_diagnostic_id: saved.source_diagnostic_id || null
    };
    progressSection.hidden = false;
    try {
      const status = await jsonRequest(
        `/api/commercial/leads/${encodeURIComponent(leadState.lead_id)}/status`,
        { lead_id: leadState.lead_id, status_token: leadState.status_token }
      );
      const express = (status.diagnostics || []).find(item => item.product_id === "express");
      if (express) {
        leadState.source_diagnostic_id = express.diagnostic_id;
        const savedIdentity = identity();
        savedIdentity.lead_id = leadState.lead_id;
        savedIdentity.source_diagnostic_id = express.diagnostic_id;
        sessionStorage.setItem(stateKey, JSON.stringify(savedIdentity));
      }
      await refreshStatus();
      const latest = (status.diagnostics || []).at(-1);
      if (latest?.status === "done" && latest.product_id === "express") {
        byId("upgrade-actions").hidden = false;
      }
    } catch (error) {
      showError(byId("progress-message"), error.message);
    }
  };

  Promise.all([
    fetch("/api/commercial/config", { cache: "no-store" }).then(response => response.json()),
    fetch("/api/landing/config", { cache: "no-store" }).then(response => response.json())
  ]).then(([commercial, landing]) => {
    config = commercial;
    privacyConfig = landing;
    products = Object.fromEntries((commercial.products || []).map(item => [item.id, item]));
    renderCatalog();
    if (landing.privacy_policy_url) byId("privacy-link").href = landing.privacy_policy_url;
    const journeyEnabled =
      commercial.simulator_enabled || commercial.real_express_enabled;
    if (
      journeyEnabled
      && landing.campaign_id
      && landing.privacy_policy_url
      && landing.privacy_policy_version
    ) {
      byId("environment-note").textContent = commercial.simulator_enabled
        ? "Homologação · dados sintéticos"
        : "Express · fila n8n Standard Normal";
      byId("search-button").disabled = false;
      restoreCapability();
    } else {
      byId("commercial-unavailable").hidden = false;
      byId("commercial-unavailable").textContent =
        "A jornada está bloqueada. Configure campanha e política; o caminho real também exige todas as aprovações de fornecedor, armazenamento, orçamento e n8n.";
      byId("search-button").disabled = true;
    }
  }).catch(() => {
    byId("search-button").disabled = true;
    showError(byId("commercial-unavailable"), "Não foi possível carregar as configurações da homologação.");
    byId("commercial-unavailable").hidden = false;
  });

  searchForm.addEventListener("submit", async event => {
    event.preventDefault();
    searchError.textContent = "";
    if (
      (!config?.simulator_enabled && !config?.real_express_enabled)
      || !searchForm.reportValidity()
    ) return;
    byId("search-button").disabled = true;
    candidateSection.hidden = true;
    leadSection.hidden = true;
    try {
      search = makeSearch();
      const result = await jsonRequest("/api/business-finder/search", search);
      candidateList.replaceChildren();
      for (const [index, candidate] of result.candidates.entries()) {
        candidateList.append(candidateCard(candidate, index));
      }
      candidateSection.hidden = false;
      if (result.candidates.length === 0) {
        const note = document.createElement("p");
        note.textContent = result.message || "Não foi possível localizar candidatos nesta pesquisa.";
        candidateList.append(note);
        const assisted = document.createElement("button");
        assisted.type = "button";
        assisted.className = "submit-button";
        assisted.textContent = "Solicitar atendimento assistido";
        assisted.addEventListener("click", openAssistedForm, { once: true });
        candidateList.append(assisted);
      } else {
        const clarification = document.createElement("p");
        clarification.textContent = result.message;
        candidateList.prepend(clarification);
      }
    } catch (error) {
      showError(searchError, error.message);
    } finally {
      byId("search-button").disabled = false;
    }
  });

  leadForm.addEventListener("submit", async event => {
    event.preventDefault();
    leadError.textContent = "";
    if (!leadForm.reportValidity() || !search) return;
    byId("lead-button").disabled = true;
    try {
      if (!leadSubmission) {
        const data = new FormData(leadForm);
        const person = identity();
        leadSubmission = {
          request_id: person.request_id,
          status_token: person.status_token,
          business_confirmed: leadType === "confirmed",
          ...(leadType === "confirmed"
            ? {
                confirmation_token: selectedConfirmation,
              search_request_id: search.request_id,
              business_search: search
              }
            : {}),
          business_name: String(leadForm.elements.business_name.value || search.business_name).trim(),
          city: search.city,
          state: search.state,
          address: search.address,
          google_maps_url: search.google_maps_url,
          contact_name: String(data.get("contact_name") || "").trim(),
          email: String(data.get("email") || "").trim(),
          whatsapp: String(data.get("whatsapp") || "").trim(),
          campaign_id: privacyConfig.campaign_id,
          privacy_acknowledged: data.get("privacy_acknowledged") === "on",
          marketing_consent: data.get("marketing_consent") === "on",
          website: String(data.get("website") || "")
        };
      }
      const lead = await jsonRequest("/api/commercial/leads", leadSubmission);
      leadState = {
        lead_id: lead.lead_id,
        status_token: lead.status_token,
        source_diagnostic_id: null
      };
      leadType = lead.lead_type;
      const person = identity();
      person.lead_id = lead.lead_id;
      sessionStorage.setItem(stateKey, JSON.stringify(person));
      leadSubmission = null;
      leadSection.hidden = true;
      progressSection.hidden = false;
      if (leadType === "assisted") {
        await refreshStatus();
        return;
      }
      diagnosticSubmission = diagnosticSubmission || {
        request_id: crypto.randomUUID(),
        lead_id: lead.lead_id,
        status_token: lead.status_token,
        product_id: "express",
        confirmation_token: selectedConfirmation,
        search,
        keyword: String(leadForm.elements.keyword.value || "").trim(),
        confirmed: true
      };
      const diagnostic = await jsonRequest("/api/commercial/diagnostics", diagnosticSubmission);
      leadState.source_diagnostic_id = diagnostic.diagnostic.diagnostic_id;
      person.source_diagnostic_id = diagnostic.diagnostic.diagnostic_id;
      sessionStorage.setItem(stateKey, JSON.stringify(person));
      diagnosticSubmission = null;
      await pollStatus();
    } catch (error) {
      showError(leadError, error.message);
      byId("lead-button").disabled = false;
    }
  });

  byId("refresh-button").addEventListener("click", async () => {
    try {
      await refreshStatus();
    } catch (error) {
      showError(byId("progress-message"), error.message);
    }
  });
  byId("professional-button").addEventListener("click", () => createOrder("professional"));
  byId("monitor-button").addEventListener("click", () => createOrder("monitor"));
})();
