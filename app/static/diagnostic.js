(() => {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const requestId = fragment.get("request_id");
  const token = fragment.get("token");
  const statusMessage = document.getElementById("status-message");
  const statusError = document.getElementById("status-error");
  const details = document.getElementById("status-details");
  const refresh = document.getElementById("refresh-status");
  const reportLink = document.getElementById("report-link");
  const reportPanel = document.getElementById("report");
  let currentStatus = null;

  const labels = {
    queued: "Na fila",
    claimed: "Preparando a pesquisa",
    business_resolved: "Validando a empresa",
    scan_created: "Preparando os pontos",
    submitting: "Enviando tarefas para processamento",
    submitted: "Tarefas enviadas",
    processing: "Diagnóstico em processamento",
    partial: "Diagnóstico concluído parcialmente",
    done: "Diagnóstico concluído",
    failed: "Não foi possível concluir o diagnóstico",
    budget_blocked: "Aguardando liberação operacional",
    manual_review: "Em revisão manual",
    submission_uncertain: "Em reconciliação"
  };

  function showError(message) {
    statusError.textContent = message;
  }

  async function loadStatus() {
    statusError.textContent = "";
    if (!requestId || !token || !/^[a-f0-9]{64}$/.test(token)) {
      statusMessage.textContent = "O link individual está incompleto ou inválido.";
      return;
    }
    try {
      const response = await fetch(`/api/landing/status/${encodeURIComponent(requestId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ status_token: token })
      });
      const result = await response.json();
      if (!response.ok || result.authorized !== true) {
        throw new Error("Não foi possível validar este link. Confira se ele está completo.");
      }
      currentStatus = result;
      document.getElementById("status-protocol").textContent = result.request_id;
      document.getElementById("status-label").textContent = labels[result.status] || "Em processamento";
      document.getElementById("status-progress").textContent = `${result.progress} de ${result.total}`;
      details.hidden = false;
      refresh.hidden = false;
      statusMessage.textContent = result.finished
        ? "O processamento foi encerrado. Consulte o diagnóstico quando estiver disponível."
        : "Seu diagnóstico está sendo preparado. Você pode voltar a esta página para consultar o andamento.";
      reportLink.hidden = !result.report_ready;
      reportLink.href = "#";
    } catch (requestError) {
      showError(requestError instanceof Error ? requestError.message : "Falha temporária ao consultar o status.");
      statusMessage.textContent = "";
    }
  }

  function renderReport(data) {
    const container = document.getElementById("report-content");
    container.replaceChildren();
    const analysis = data.analysis || {};
    const metrics = analysis.metrics || data.metrics || {};
    const coverage = data.coverage || {};
    const metricGrid = document.createElement("div");
    metricGrid.className = "metric-grid";
    const entries = [
      ["Cobertura válida", `${coverage.coverage_pct ?? 0}%`],
      ["Pontos válidos", `${coverage.valid_points ?? 0} / ${coverage.requested_points ?? 0}`],
      ...Object.entries(metrics).filter(([, value]) => ["string", "number"].includes(typeof value))
    ];
    for (const [label, value] of entries.slice(0, 12)) {
      const card = document.createElement("div");
      card.className = "metric-card";
      const title = document.createElement("span");
      title.textContent = String(label).replaceAll("_", " ");
      const amount = document.createElement("strong");
      amount.textContent = String(value);
      card.append(title, amount);
      metricGrid.append(card);
    }
    container.append(metricGrid);
    const summary = document.createElement("p");
    summary.className = "report-summary";
    summary.textContent = `Empresa: ${data.business?.name || "Empresa participante"} · Palavra-chave: ${data.keyword || "—"}. Os indicadores usam os pontos válidos informados no diagnóstico.`;
    container.append(summary);
  }

  async function loadReport() {
    if (!currentStatus?.report_ready) return;
    statusError.textContent = "";
    reportLink.textContent = "Carregando diagnóstico...";
    reportLink.setAttribute("aria-disabled", "true");
    try {
      const response = await fetch(`/api/landing/report/${encodeURIComponent(requestId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ status_token: token })
      });
      const result = await response.json();
      if (!response.ok || result.authorized !== true || !result.report) {
        throw new Error("O diagnóstico ainda não está disponível. Tente novamente.");
      }
      renderReport(result.report);
      reportPanel.hidden = false;
      reportPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (requestError) {
      showError(requestError instanceof Error ? requestError.message : "Falha ao carregar o diagnóstico.");
    } finally {
      reportLink.textContent = "Ver diagnóstico →";
      reportLink.removeAttribute("aria-disabled");
    }
  }

  refresh.addEventListener("click", loadStatus);
  reportLink.addEventListener("click", event => {
    event.preventDefault();
    loadReport();
  });
  document.getElementById("print-report").addEventListener("click", () => window.print());
  loadStatus();
})();
