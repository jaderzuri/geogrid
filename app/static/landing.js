(() => {
  const form = document.getElementById("lead-form");
  if (!form) return;

  const error = document.getElementById("form-error");
  const button = document.getElementById("submit-button");
  const confirmation = document.getElementById("confirmation");
  const policyLink = document.getElementById("privacy-link");
  let campaignId = "";
  let enabled = false;
  let pendingSubmission = null;

  const bytesToHex = bytes =>
    Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");

  const pendingIdentity = () => {
    let identity;
    try {
      identity = JSON.parse(sessionStorage.getItem("zuri-pending-identity") || "null");
    } catch {
      identity = null;
    }
    if (!identity || !identity.request_id || !identity.status_token) {
      const tokenBytes = new Uint8Array(32);
      crypto.getRandomValues(tokenBytes);
      identity = { request_id: crypto.randomUUID(), status_token: bytesToHex(tokenBytes) };
      sessionStorage.setItem("zuri-pending-identity", JSON.stringify(identity));
    }
    return identity;
  };

  const formValues = () => {
    const data = new FormData(form);
    const identity = pendingIdentity();
    return {
      request_id: identity.request_id,
      status_token: identity.status_token,
      contact_name: String(data.get("contact_name") || "").trim(),
      company_name: String(data.get("company_name") || "").trim(),
      whatsapp: String(data.get("whatsapp") || "").trim(),
      email: String(data.get("email") || "").trim(),
      city: String(data.get("city") || "").trim(),
      state: String(data.get("state") || ""),
      google_maps_url: String(data.get("google_maps_url") || "").trim() || null,
      keyword: String(data.get("keyword") || "").trim(),
      campaign_id: campaignId,
      privacy_acknowledged: data.get("privacy_acknowledged") === "on",
      marketing_consent: data.get("marketing_consent") === "on",
      website: String(data.get("website") || "")
    };
  };

  const requestedCampaign = new URLSearchParams(window.location.search).get("campaign");
  const configUrl = new URL("/api/landing/config", window.location.origin);
  if (requestedCampaign) configUrl.searchParams.set("campaign", requestedCampaign);

  fetch(configUrl, { cache: "no-store" })
    .then(response => response.json())
    .then(config => {
      campaignId = config.campaign_id || "";
      enabled = Boolean(config.enabled && campaignId);
      form.elements.campaign_id.value = campaignId;
      if (config.privacy_policy_url) {
        policyLink.href = config.privacy_policy_url;
      } else {
        policyLink.href = "/privacy";
      }
      if (!enabled) {
        error.textContent = "O formulário está temporariamente indisponível. Tente novamente mais tarde.";
        button.disabled = true;
      }
    })
    .catch(() => {
      error.textContent = "Não foi possível carregar o formulário. Atualize a página para tentar novamente.";
      button.disabled = true;
    });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    error.textContent = "";
    if (!enabled || !form.reportValidity()) return;

    button.disabled = true;
    button.textContent = "Enviando solicitação...";
    try {
      if (!pendingSubmission) pendingSubmission = formValues();
      const response = await fetch("/api/landing/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(pendingSubmission)
      });
      const result = await response.json();
      if (!response.ok || result.accepted !== true) {
        throw new Error(result.detail || "Não foi possível enviar. Tente novamente.");
      }
      const identity = pendingIdentity();
      const statusUrl = new URL("/diagnostico", window.location.origin);
      statusUrl.hash = new URLSearchParams({
        request_id: result.request_id,
        token: identity.status_token
      }).toString();
      document.getElementById("protocol").textContent = result.request_id;
      const link = document.getElementById("status-link");
      link.href = statusUrl.toString();
      link.dataset.requestId = result.request_id;
      document.getElementById("duplicate-note").hidden = !result.duplicate;
      form.hidden = true;
      confirmation.hidden = false;
      pendingSubmission = null;
      sessionStorage.removeItem("zuri-pending-identity");
    } catch (submitError) {
      error.textContent = submitError instanceof Error
        ? submitError.message
        : "Não foi possível enviar. Tente novamente.";
      button.disabled = false;
      button.innerHTML = 'Quero meu diagnóstico gratuito <span aria-hidden="true">→</span>';
    }
  });
})();
