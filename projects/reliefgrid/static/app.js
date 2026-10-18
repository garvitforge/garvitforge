const $ = (selector, root = document) => root.querySelector(selector);
const state = { searchTimer: null, toastTimer: null };
const severityNames = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.hidden = true; }, 3600);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

async function loadDashboard() {
  const [summary, health] = await Promise.all([
    requestJSON("/api/dashboard"),
    requestJSON("/api/health").catch(() => ({ outbound_alerts_configured: false })),
  ]);
  $("#stat-active").textContent = summary.active_incidents;
  $("#stat-critical").textContent = summary.critical_incidents;
  $("#stat-units").textContent = Number(summary.available_units).toLocaleString();
  $("#stat-resolved").textContent = summary.resolved_incidents;
  const configured = Boolean(health.outbound_alerts_configured);
  const alertStatus = $("#alert-status");
  alertStatus.classList.toggle("is-ready", configured);
  $("#alert-status-title").textContent = configured ? "Alert setup detected" : "Alert setup needed";
  $("#alert-status-detail").textContent = configured
    ? "Twilio settings found · delivery not tested"
    : "SMS + voice delivery unavailable";
  const featureStatus = $("#alert-feature-status");
  featureStatus.classList.toggle("is-ready", configured);
  featureStatus.lastChild.textContent = configured
    ? "Twilio settings detected · test delivery"
    : "Twilio setup required to deliver";
}

function renderIncident(incident) {
  const card = make("article", "incident-card");
  const main = make("div", "incident-main");
  const top = make("div", "incident-top");
  top.append(make("span", "incident-code", incident.code));
  const severity = make("span", `severity severity-${incident.severity}`, severityNames[incident.severity] || incident.severity);
  top.append(severity, make("span", "incident-type", incident.kind));
  main.append(top, make("p", "incident-description", incident.description));
  const meta = make("div", "incident-meta");
  meta.append(make("span", "", `⌖ ${incident.district}`));
  meta.append(make("span", "", `Reported ${formatDate(incident.created_at)}`));
  meta.append(make("span", "", incident.status === "active" ? "● Active" : "✓ Resolved"));
  if (incident.acknowledged_at) meta.append(make("span", "", `Acknowledged ${formatDate(incident.acknowledged_at)}`));
  main.append(meta);

  const actions = make("div", "incident-actions");
  const priority = document.createElement("select");
  priority.className = "priority-select";
  priority.setAttribute("aria-label", `Set priority for ${incident.code}`);
  for (const value of ["critical", "high", "medium", "low"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = severityNames[value];
    option.selected = value === incident.severity;
    priority.append(option);
  }
  priority.addEventListener("change", async () => {
    priority.disabled = true;
    try {
      await requestJSON(`/api/incidents/${incident.id}`, { method: "PATCH", body: JSON.stringify({ severity: priority.value }) });
      showToast(`${incident.code} priority updated.`);
      await refreshDashboard();
    } catch (error) {
      priority.value = incident.severity;
      showToast(error.message, true);
    } finally { priority.disabled = false; }
  });
  actions.append(priority);

  if (incident.severity === "critical" && incident.status === "active" && !incident.acknowledged_at) {
    const acknowledgeButton = make("button", "status-button acknowledge", "Acknowledge");
    acknowledgeButton.type = "button";
    acknowledgeButton.setAttribute("aria-label", `Acknowledge critical incident ${incident.code}`);
    acknowledgeButton.addEventListener("click", async () => {
      acknowledgeButton.disabled = true;
      try {
        await requestJSON(`/api/incidents/${incident.id}`, {
          method: "PATCH",
          body: JSON.stringify({ acknowledge: true }),
        });
        showToast(`${incident.code} acknowledged.`);
        await refreshDashboard();
      } catch (error) {
        showToast(error.message, true);
      } finally { acknowledgeButton.disabled = false; }
    });
    actions.append(acknowledgeButton);
  }

  const statusButton = make("button", `status-button ${incident.status === "active" ? "resolve" : "reopen"}`, incident.status === "active" ? "Resolve" : "Reopen");
  statusButton.type = "button";
  statusButton.addEventListener("click", async () => {
    statusButton.disabled = true;
    const nextStatus = incident.status === "active" ? "resolved" : "active";
    try {
      await requestJSON(`/api/incidents/${incident.id}`, { method: "PATCH", body: JSON.stringify({ status: nextStatus }) });
      showToast(`${incident.code} marked ${nextStatus}.`);
      await refreshDashboard();
    } catch (error) {
      showToast(error.message, true);
    } finally { statusButton.disabled = false; }
  });
  actions.append(statusButton);
  card.append(main, actions);
  return card;
}

async function loadIncidents() {
  const list = $("#incident-list");
  const error = $("#incident-error");
  list.setAttribute("aria-busy", "true");
  error.hidden = true;
  const params = new URLSearchParams({
    q: $("#search").value.trim(),
    status: $("#status-filter").value,
    severity: $("#priority-filter").value,
  });
  try {
    const result = await requestJSON(`/api/incidents?${params}`);
    list.replaceChildren();
    $("#queue-count").textContent = `${result.total} report${result.total === 1 ? "" : "s"} match these filters`;
    if (!result.items.length) {
      list.append(make("p", "empty-state", "No reports match. Try changing the filters or submit a new report."));
      return;
    }
    for (const incident of result.items) list.append(renderIncident(incident));
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    list.replaceChildren(make("p", "empty-state", "Could not load the incident queue."));
  } finally { list.setAttribute("aria-busy", "false"); }
}

async function loadResources() {
  const list = $("#resource-list");
  const error = $("#resource-error");
  list.setAttribute("aria-busy", "true");
  error.hidden = true;
  try {
    const resources = await requestJSON("/api/resources");
    list.replaceChildren();
    for (const resource of resources) {
      const card = make("article", "resource-card");
      const top = make("div", "resource-top");
      top.append(make("span", "resource-name", resource.name));
      top.append(make("span", "resource-amount", `${resource.available} / ${resource.capacity}`));
      const meter = document.createElement("progress");
      meter.className = "resource-meter";
      meter.max = Math.max(1, resource.capacity);
      meter.value = Math.min(resource.available, resource.capacity);
      meter.setAttribute("aria-label", `${resource.name}: ${resource.available} available of ${resource.capacity}`);
      const ratio = resource.capacity ? resource.available / resource.capacity : 0;
      const stateName = ratio === 0 ? "No units available" : ratio < 0.25 ? "Low stock" : "Available";
      const stateClass = ratio === 0 ? "empty" : ratio < 0.25 ? "low" : "";
      card.append(top, meter, make("span", `resource-state ${stateClass}`, stateName), make("span", "resource-location", resource.location));
      list.append(card);
    }
    if (!resources.length) list.append(make("p", "empty-state", "No inventory items have been configured."));
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    list.replaceChildren(make("p", "empty-state", "Could not load resource availability."));
  } finally { list.setAttribute("aria-busy", "false"); }
}

async function refreshDashboard() {
  const results = await Promise.allSettled([loadDashboard(), loadIncidents(), loadResources()]);
  const failed = results.some(result => result.status === "rejected");
  if (failed) showToast("Some dashboard data could not be refreshed.", true);
}

const reportDialog = $("#report-dialog");
$("#open-report").addEventListener("click", () => {
  $("#form-error").hidden = true;
  reportDialog.showModal();
  $("[name='kind']", reportDialog).focus();
});
$("#close-dialog").addEventListener("click", () => reportDialog.close());
$("#cancel-report").addEventListener("click", () => reportDialog.close());
reportDialog.addEventListener("click", event => {
  if (event.target === reportDialog) reportDialog.close();
});
$("#report-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = $("#submit-report");
  const error = $("#form-error");
  error.hidden = true;
  submit.disabled = true;
  submit.textContent = "Saving…";
  const data = new FormData(form);
  const payload = Object.fromEntries(data.entries());
  payload.kind = String(payload.kind || "").trim();
  payload.description = String(payload.description || "").trim();
  payload.district = String(payload.district || "").trim();
  try {
    const report = await requestJSON("/api/incidents", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    reportDialog.close();
    $("#search").value = "";
    $("#status-filter").value = "active";
    $("#priority-filter").value = "all";
    await refreshDashboard();
    showToast(`Report ${report.code} saved to the queue.`);
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Submit report";
  }
});

$("#filters").addEventListener("submit", event => event.preventDefault());
$("#filters").addEventListener("reset", () => {
  setTimeout(() => { $("#search").value = ""; $("#status-filter").value = "active"; $("#priority-filter").value = "all"; loadIncidents(); }, 0);
});
$("#search").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(loadIncidents, 220);
});
$("#status-filter").addEventListener("change", loadIncidents);
$("#priority-filter").addEventListener("change", loadIncidents);
$("#refresh").addEventListener("click", refreshDashboard);

refreshDashboard();
