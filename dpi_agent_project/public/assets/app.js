/* DPI Public Agent – website controller.
   Plain JavaScript, no framework or build step.  All dynamic text is set with
   textContent (never innerHTML), so data from the API cannot inject markup. */
"use strict";

(() => {
  // ------------------------------------------------------------ helpers
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function icon(name, className = "icon") {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  const store = {
    get(key, fallback) {
      try {
        const value = localStorage.getItem(`dpi.${key}`);
        return value === null ? fallback : JSON.parse(value);
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(`dpi.${key}`, JSON.stringify(value));
      } catch {
        /* storage unavailable (private mode): preferences just won't persist */
      }
    },
  };

  const numberFormat = new Intl.NumberFormat("en-IN");
  const timeFormat = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });
  const formatTime = (iso) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? iso || "" : timeFormat.format(date);
  };
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, reducedMotion ? 0 : ms));

  function announce(text) {
    const region = $("#sr-status");
    region.textContent = "";
    window.setTimeout(() => { region.textContent = text; }, 50);
  }

  async function api(path, { body } = {}) {
    const init = body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    const response = await fetch(path, init);
    let data = null;
    try {
      data = await response.json();
    } catch {
      /* not JSON */
    }
    if (!response.ok) {
      const error = new Error((data && data.error) || `The service returned an error (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  const INTENTS = {
    apply_for_solar_pump_subsidy: "Solar pump subsidy",
    assess_agricultural_credit: "Farm credit check",
    unsupported: "Not a supported service",
  };
  const friendlyIntent = (intent) => INTENTS[intent] || (intent ? String(intent) : "–");
  const humanizeStatus = (status) => {
    const text = String(status || "").replace(/_/g, " ").toLowerCase();
    return text.charAt(0).toUpperCase() + text.slice(1);
  };

  function interpreterLabel(name) {
    if (!name) return "";
    if (name === "deterministic") return "Rules only";
    const [provider, ...rest] = String(name).split(":");
    const model = rest.join(":");
    if (provider === "groq") return `Cloud AI (Groq) · ${model}`;
    if (provider === "ollama") return `Local AI (Ollama) · ${model}`;
    return name;
  }

  function toneFor(status) {
    const s = String(status || "").toUpperCase();
    if (s.includes("COMPLETED") || s.includes("GRANTED") || s === "ACTIVE") return "success";
    if (s.includes("ESCALAT") || s.includes("NEEDS") || s.includes("SUSPENDED") || s.includes("EXPIRED")) return "warn";
    if (s.includes("DENIED") || s.includes("FAIL") || s.includes("ERROR") || s.includes("REVOKED")) return "danger";
    return "neutral";
  }
  const statusTag = (status) => el("span", { class: `tag tag--${toneFor(status)}`, text: humanizeStatus(status) || "–" });

  // ------------------------------------------------------------ preferences
  const root = document.documentElement;

  function applyFont(step) {
    const value = Math.max(-1, Math.min(2, step));
    root.dataset.font = String(value);
    store.set("font", value);
    return value;
  }

  function applyContrast(high) {
    if (high) root.dataset.contrast = "high";
    else delete root.dataset.contrast;
    $("#contrast-toggle").setAttribute("aria-pressed", String(high));
    store.set("contrast", high);
  }

  function setupPreferences() {
    applyFont(store.get("font", 0));
    applyContrast(store.get("contrast", false));
    $$("[data-font-step]").forEach((button) => {
      button.addEventListener("click", () => {
        const step = Number(button.dataset.fontStep);
        const current = Number(root.dataset.font || 0);
        const value = applyFont(step === 0 ? 0 : current + step);
        announce(["Smaller text", "Default text size", "Larger text", "Largest text"][value + 1]);
      });
    });
    $("#contrast-toggle").addEventListener("click", () => {
      const high = root.dataset.contrast !== "high";
      applyContrast(high);
      announce(high ? "High contrast on" : "High contrast off");
    });
  }

  // ------------------------------------------------------------ navigation
  const views = $$(".view");
  const onEnter = {};
  let firstRoute = true;

  function closeMenu() {
    $("#menu-toggle").setAttribute("aria-expanded", "false");
    $("#primary-nav").classList.remove("is-open");
  }

  function route() {
    const name = (location.hash || "#home").slice(1);
    const target = views.find((v) => v.id === `view-${name}`);
    if (!target && !firstRoute) return; // not a page, e.g. the skip link's #main
    const view = target || $("#view-home");
    const page = view.id.slice("view-".length);
    views.forEach((v) => { v.hidden = v !== view; });
    $$(".primary-nav a").forEach((link) => {
      if (link.getAttribute("href") === `#${page}`) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    document.title = `${view.dataset.title} – DPI Public Agent`;
    closeMenu();
    window.scrollTo({ top: 0 });
    if (!firstRoute) $("h1", view).focus({ preventScroll: true });
    firstRoute = false;
    if (onEnter[page]) onEnter[page]();
  }

  function setupNavigation() {
    const toggle = $("#menu-toggle");
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      $("#primary-nav").classList.toggle("is-open", open);
    });
    window.addEventListener("hashchange", route);
    route();
  }

  // ------------------------------------------------------------ service status
  let health = null;

  const OPTION_TEXT = {
    deterministic: () => "Always available",
    ollama: (info) => {
      if (info.available) return info.model;
      if (health && health.deployment === "vercel") return "Only when you run the app on your own computer";
      return "Not running: start Ollama";
    },
    groq: (info) => (info.available ? info.model : "Not configured"),
  };

  function selectedProvider() {
    const checked = $('input[name="provider"]:checked');
    return checked ? checked.value : "deterministic";
  }

  function updateHeaderStatus() {
    const status = $("#header-status");
    const name = selectedProvider();
    const info = health && health.providers ? health.providers[name] : null;
    if (!health) {
      status.dataset.state = "warn";
      $("#header-status-text").textContent = "Service unavailable";
      return;
    }
    status.dataset.state = info && info.available ? "ok" : "warn";
    const label = name === "deterministic" ? "Rules only" : interpreterLabel(`${name}:${info && info.model ? info.model : ""}`);
    $("#header-status-text").textContent = `Reading requests: ${label}`;
  }

  function renderProviders() {
    const providers = (health && health.providers) || { deterministic: { available: true } };
    for (const option of $$("[data-provider-option]")) {
      const name = option.dataset.providerOption;
      const info = providers[name] || { available: false };
      const input = $("input", option);
      const state = $("[data-state]", option);
      input.disabled = !info.available;
      state.textContent = (OPTION_TEXT[name] || (() => ""))(info);
      state.classList.toggle("is-off", !info.available);
      state.classList.toggle("mono", Boolean(info.available && info.model)); // model names only
      if (!info.available && info.error) option.title = info.error;
    }
    const preferred = [store.get("provider", null), health && health.default_provider, "deterministic"];
    const choice = preferred.find((name) => name && providers[name] && providers[name].available) || "deterministic";
    const input = $(`input[name="provider"][value="${choice}"]`);
    if (input) input.checked = true;
    updateHeaderStatus();
  }

  function renderStats() {
    if (!health) return;
    const tables = (health.database && health.database.tables) || {};
    const records = Object.values(tables).reduce((sum, n) => sum + n, 0);
    const providers = Object.values(health.providers || {});
    $("#stat-records").textContent = records ? numberFormat.format(records) : "–";
    $("#stat-ai").textContent = `${providers.filter((p) => p.available).length} of ${providers.length}`;
    $("#stat-audit").textContent = numberFormat.format((health.audit_records || {}).workflow || 0);
    $("#stat-threshold").textContent = `${Math.round((health.config.confidence_threshold || 0) * 100)}%`;
    $("#rule-acres").textContent = health.config.min_land_area_acres;
    $("#rule-score").textContent = health.config.min_credit_score;
    $("#footer-version").textContent = `Version ${health.version}${health.deployment === "vercel" ? " · cloud demo" : ""}`;
  }

  async function loadHealth() {
    try {
      health = await api("/api/health");
    } catch {
      health = null;
    }
    renderProviders();
    renderStats();
  }

  // ------------------------------------------------------------ conversation
  const STAGES = ["interpret", "triage", "identity", "consent", "records", "policy", "risk", "record"];
  const STATE_LABELS = { active: "Checking…", pass: "Passed", info: "Done", warn: "Attention", fail: "Stopped", skipped: "Not needed" };
  let busy = false;
  let lastResult = null;

  function addMessage(role, paragraphs, meta) {
    const lines = Array.isArray(paragraphs) ? paragraphs : [paragraphs];
    const message = el("div", { class: `msg msg--${role}` },
      el("div", { class: "msg-avatar", "aria-hidden": "true" }, icon(role === "user" ? "person" : "chat")),
      el("div", { class: "msg-bubble" },
        el("p", { class: "msg-author", text: role === "user" ? "You" : "Public Agent" }),
        lines.map((line) => el("p", { text: line })),
        meta ? el("p", { class: "msg-meta", text: meta }) : null));
    const box = $("#conversation");
    box.append(message);
    box.scrollTop = box.scrollHeight;
    return message;
  }

  function addTyping() {
    const typing = el("div", { class: "msg msg--agent msg--typing" },
      el("div", { class: "msg-avatar", "aria-hidden": "true" }, icon("chat")),
      el("div", { class: "msg-bubble" },
        el("span", { class: "visually-hidden", text: "The agent is working on your request" }),
        el("span", { class: "dot" }), el("span", { class: "dot" }), el("span", { class: "dot" })));
    const box = $("#conversation");
    box.append(typing);
    box.scrollTop = box.scrollHeight;
    return typing;
  }

  function trackerItem(stage) {
    return $(`#tracker li[data-stage="${stage}"]`);
  }

  function setStage(stage, state, detail) {
    const item = trackerItem(stage);
    item.dataset.state = state;
    $(".tracker-status", item).textContent = STATE_LABELS[state] || "Waiting";
    if (detail) $(".tracker-detail", item).textContent = detail;
  }

  function resetTracker() {
    for (const item of $$("#tracker li")) {
      item.dataset.state = "";
      $(".tracker-status", item).textContent = "Waiting";
      $(".tracker-detail", item).textContent = item.dataset.default;
    }
  }

  function stageDetail(stage, steps) {
    if (stage === "interpret") {
      const intentStep = steps.find((s) => s.message.startsWith("Intent:"));
      if (intentStep) {
        const intent = intentStep.message.replace("Intent:", "").trim();
        const confidence = (intentStep.detail.match(/Confidence: ([\d.]+)/) || [])[1];
        return `${friendlyIntent(intent)}${confidence ? ` · confidence ${confidence}` : ""}`;
      }
    }
    const last = steps[steps.length - 1];
    return last.status === "fail" || last.status === "warn" ? last.message : last.detail || last.message;
  }

  async function playTracker(steps) {
    const byStage = {};
    for (const step of steps) (byStage[step.stage || "interpret"] ||= []).push(step);
    for (const stage of STAGES) {
      const stageSteps = byStage[stage];
      if (!stageSteps) {
        setStage(stage, "skipped");
        continue;
      }
      setStage(stage, "active");
      await pause(260);
      const statuses = stageSteps.map((s) => s.status);
      const state = ["fail", "warn", "pass"].find((s) => statuses.includes(s)) || "info";
      setStage(stage, state, stageDetail(stage, stageSteps));
    }
  }

  function describeOutcome(result) {
    const facts = [];
    const escalation = result.escalation || {};
    const consent = result.consent || {};
    let outcome;
    switch (result.status) {
      case "COMPLETED":
        outcome = {
          tone: "success", icon: "check",
          title: result.credit_summary ? "Assessment complete" : "Approved and recorded",
          text: result.message,
        };
        if (result.citizen) facts.push(["Citizen", result.citizen.name]);
        if (result.transaction && result.transaction.transaction_id) facts.push(["Transaction", result.transaction.transaction_id, true]);
        if (result.credit_summary) facts.push(["Risk tier", result.credit_summary.risk_tier]);
        if (consent.consent_id) facts.push(["Consent used", consent.consent_id, true]);
        break;
      case "DENIED_CONSENT":
        outcome = {
          tone: "info", icon: "key", title: "Your consent is needed",
          text: "Your consent on record does not allow this data to be used for this purpose, so none of it was read. Grant the missing permission in your consent manager, then ask again.",
        };
        facts.push(["Reason", consent.reason || "Consent not valid"]);
        break;
      case "DENIED_INELIGIBLE":
        outcome = {
          tone: "danger", icon: "x", title: "Not eligible",
          text: ((result.eligibility && result.eligibility.reasons) || ["The eligibility rules were not met."]).join(" "),
        };
        break;
      case "ESCALATED":
        outcome = {
          tone: "warn", icon: "person", title: "Sent to an officer for review",
          text: "This request could not be completed automatically, so an officer will review it. Nothing was decided by software.",
        };
        if (escalation.reason) facts.push(["Why", escalation.reason]);
        if (escalation.case_id) facts.push(["Case reference", escalation.case_id, true]);
        break;
      case "NEEDS_NATIONAL_ID":
        outcome = { tone: "info", icon: "id", title: "Please add your national ID", text: result.message };
        break;
      case "FAILED_VALIDATION":
        outcome = {
          tone: "danger", icon: "alert", title: "We could not understand the request",
          text: "Please rephrase it in a short sentence and include your national ID.",
        };
        break;
      case "FAILED_NO_LAND_RECORD":
      case "FAILED_NO_CREDIT_RECORD":
        outcome = { tone: "danger", icon: "file", title: "No record found", text: result.error || "The registry has no record for this ID." };
        break;
      default:
        outcome = { tone: "danger", icon: "alert", title: "Something went wrong", text: result.error || "Please try again." };
    }
    if (result.interpreter) facts.push(["Read by", interpreterLabel(result.interpreter)]);
    if (result.workflow_id) facts.push(["Reference", result.workflow_id, true]);
    return { ...outcome, facts };
  }

  function showOutcome(result) {
    const outcome = describeOutcome(result);
    const box = $("#outcome");
    box.dataset.tone = outcome.tone;
    $(".outcome-icon use", box).setAttribute("href", `#i-${outcome.icon}`);
    $("#outcome-title").textContent = outcome.title;
    $("#outcome-text").textContent = outcome.text || "";
    const facts = $("#outcome-facts");
    facts.replaceChildren(...outcome.facts.flatMap(([label, value, mono]) => [
      el("dt", { text: label }),
      el("dd", { text: value, class: mono ? "mono" : null }),
    ]));
    box.hidden = false;
    return outcome;
  }

  function agentReply(result, outcome) {
    const lines = [outcome.title + ".", outcome.text];
    if (result.status === "ESCALATED" && result.escalation && result.escalation.case_id) {
      lines.push(`Your case reference is ${result.escalation.case_id}.`);
    }
    const fellBack = (result.steps || []).some((s) => s.message.startsWith("Model unavailable"));
    if (fellBack) lines.push("The AI service was unavailable, so the keyword rules read your request instead.");
    return lines.filter(Boolean);
  }

  function setBusy(isBusy) {
    busy = isBusy;
    const button = $("#send-btn");
    button.disabled = isBusy;
    button.classList.toggle("is-busy", isBusy);
    $(".btn-label", button).textContent = isBusy ? "Working" : "Send";
    $("#conversation").setAttribute("aria-busy", String(isBusy));
  }

  async function send(text, providerOverride) {
    const query = text.trim();
    if (!query || busy) return;
    setBusy(true);
    addMessage("user", query);
    $("#query").value = "";
    updateCount();
    resetTracker();
    $("#outcome").hidden = true;
    setStage("interpret", "active");
    const typing = addTyping();
    const provider = providerOverride || selectedProvider();
    try {
      const result = await api("/api/chat", { body: { query, provider } });
      typing.remove();
      await playTracker(result.steps || []);
      lastResult = result;
      const outcome = showOutcome(result);
      const demoNote = providerOverride ? " · demo example, read by the rules" : "";
      addMessage("agent", agentReply(result, outcome), `${interpreterLabel(result.interpreter) || "Rules only"}${demoNote}`);
    } catch (error) {
      typing.remove();
      resetTracker();
      addMessage("agent", error.status === 429
        ? [error.message]
        : ["Sorry, the service could not be reached.", error.message]);
    } finally {
      setBusy(false);
    }
  }

  function updateCount() {
    $("#query-count").textContent = `${$("#query").value.length} / 2000`;
  }

  function setupConversation() {
    for (const item of $$("#tracker li")) item.dataset.default = $(".tracker-detail", item).textContent;
    $("#composer").addEventListener("submit", (event) => {
      event.preventDefault();
      send($("#query").value);
    });
    $("#query").addEventListener("input", updateCount);
    $("#query").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        $("#composer").requestSubmit();
      }
    });
    $$('input[name="provider"]').forEach((input) => input.addEventListener("change", () => {
      store.set("provider", input.value);
      updateHeaderStatus();
    }));
    $("#outcome-details").addEventListener("click", () => {
      if (lastResult) openRecord("Full workflow record", lastResult);
    });
    // Example buttons: chips fill the box; other buttons go to the agent and send.
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-example]");
      if (!trigger) return;
      const text = trigger.dataset.example;
      if (location.hash !== "#ask") location.hash = "#ask";
      if (trigger.classList.contains("chip")) {
        $("#query").value = text;
        updateCount();
        $("#query").focus();
      } else {
        send(text, trigger.dataset.provider);
      }
    });
    updateCount();
  }

  // ------------------------------------------------------------ tabs
  function setupTabs(tablist, onSelect) {
    const tabs = $$('[role="tab"]', tablist);
    const select = (tab, focus) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
      });
      document.getElementById(tab.getAttribute("aria-controls")).setAttribute("aria-labelledby", tab.id);
      if (focus) tab.focus();
      onSelect(tab);
    };
    tabs.forEach((tab) => tab.addEventListener("click", () => select(tab)));
    tablist.addEventListener("keydown", (event) => {
      const index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      const target = {
        ArrowRight: tabs[(index + 1) % tabs.length],
        ArrowLeft: tabs[(index - 1 + tabs.length) % tabs.length],
        Home: tabs[0],
        End: tabs[tabs.length - 1],
      }[event.key];
      if (target) {
        event.preventDefault();
        select(target, true);
      }
    });
  }

  function renderTable(table, columns, rows, onView) {
    const head = el("tr", {}, columns.map(([label]) => el("th", { scope: "col", text: label })), el("th", { scope: "col" }, el("span", { class: "visually-hidden", text: "Details" })));
    $("thead", table).replaceChildren(head);
    const body = $("tbody", table);
    if (!rows.length) {
      body.replaceChildren(el("tr", {}, el("td", { class: "empty", colspan: columns.length + 1, text: "No records yet." })));
      return;
    }
    body.replaceChildren(...rows.map((row) => el("tr", {},
      columns.map(([, render, className]) => {
        const value = render(row);
        const truncated = Boolean(className && className.includes("truncate")) && typeof value === "string";
        return el("td", { class: className, title: truncated ? value : null }, value ?? "–");
      }),
      el("td", {}, el("button", { type: "button", class: "btn btn--secondary", text: "View", onclick: () => onView(row) })))));
  }

  // ------------------------------------------------------------ transparency
  const AUDIT_COLUMNS = {
    workflow: [
      ["Time", (r) => formatTime(r.timestamp)],
      ["Reference", (r) => r.workflow_id, "mono"],
      ["Service", (r) => friendlyIntent(r.intent)],
      ["Read by", (r) => interpreterLabel(r.interpreter) || "–"],
      ["Result", (r) => statusTag(r.final_status)],
    ],
    consent: [
      ["Time", (r) => formatTime(r.timestamp)],
      ["Citizen ID", (r) => r.national_id, "mono"],
      ["Consent", (r) => r.consent_id, "mono"],
      ["Decision", (r) => statusTag(r.result)],
      ["Reason", (r) => r.reason, "truncate"],
    ],
    escalations: [
      ["Time", (r) => formatTime(r.timestamp)],
      ["Case", (r) => r.case_id, "mono"],
      ["Citizen ID", (r) => r.national_id, "mono"],
      ["Reason", (r) => r.reason, "truncate"],
    ],
  };
  const AUDIT_TITLES = { workflow: "Request record", consent: "Consent decision", escalations: "Officer referral" };
  let auditType = "workflow";
  let auditRecords = [];

  function renderAudit() {
    const filter = $("#audit-filter").value.trim().toLowerCase();
    const rows = filter ? auditRecords.filter((r) => JSON.stringify(r).toLowerCase().includes(filter)) : auditRecords;
    renderTable($("#audit-table"), AUDIT_COLUMNS[auditType], rows, (row) => openRecord(AUDIT_TITLES[auditType], row));
    $("#audit-count").textContent = `${numberFormat.format(rows.length)} of ${numberFormat.format(auditRecords.length)} most recent records`;
  }

  async function loadAudit() {
    try {
      const data = await api(`/api/audit/${auditType}?limit=200`);
      auditRecords = data.records || [];
    } catch (error) {
      auditRecords = [];
      $("#audit-count").textContent = error.message;
    }
    renderAudit();
    $("#audit-note").textContent = health && health.deployment === "vercel"
      ? "This is a cloud demo: records are kept in temporary storage and reset when the server restarts."
      : "";
  }

  async function verifyAudit() {
    const text = $("#integrity-text");
    text.className = "integrity-text";
    text.textContent = "Checking…";
    try {
      const result = await api("/api/audit/verify");
      const logs = Object.entries(result.logs);
      const total = logs.reduce((sum, [, log]) => sum + log.records, 0);
      if (result.ok) {
        text.classList.add("is-ok");
        text.textContent = `All ${logs.length} logs are intact: ${numberFormat.format(total)} records checked, none edited, removed or reordered.`;
      } else {
        const [name, log] = logs.find(([, l]) => !l.ok);
        text.classList.add("is-bad");
        text.textContent = `Problem found in the ${name} log at line ${log.line}: ${log.error}.`;
      }
    } catch (error) {
      text.classList.add("is-bad");
      text.textContent = error.message;
    }
    announce(text.textContent);
  }

  function setupTransparency() {
    setupTabs($("#audit-tabs"), (tab) => {
      auditType = tab.dataset.audit;
      loadAudit();
    });
    $("#audit-filter").addEventListener("input", renderAudit);
    $("#verify-btn").addEventListener("click", verifyAudit);
    onEnter.transparency = loadAudit;
  }

  // ------------------------------------------------------------ open data
  const COLUMN_LABELS = {
    national_id: "National ID", full_name: "Name", phone_number: "Phone", preferred_language: "Language",
    identity_status: "Status", land_record_id: "Land record", survey_number: "Survey no.", district: "District",
    land_area_acres: "Area (acres)", current_crop: "Crop", credit_score: "Credit score",
    avg_monthly_turnover_inr: "Monthly turnover (₹)", repayment_history_status: "Repayment",
    active_gst_billing_status: "GST status", consent_id: "Consent", consent_purpose: "Purpose",
    granular_permissions: "Permissions", consent_status: "Status", valid_until: "Valid until",
    cryptographic_signature: "Signature", transaction_id: "Transaction", service_requested: "Service",
    timestamp: "Time", consent_token_used: "Consent token", slm_confidence_score: "AI confidence",
    execution_status: "Status",
  };
  const registry = { table: "identity", offset: 0, limit: 25, total: 0, query: "" };
  let registryLoaded = false;

  function registryColumns(rows) {
    if (!rows.length) return [];
    return Object.keys(rows[0]).map((key) => {
      const label = COLUMN_LABELS[key] || key;
      if (key.endsWith("_status")) return [label, (r) => statusTag(r[key])];
      if (key === "avg_monthly_turnover_inr") return [label, (r) => numberFormat.format(Math.round(r[key]))];
      if (key === "cryptographic_signature" || key === "consent_token_used") return [label, (r) => r[key], "truncate mono"];
      if (key.endsWith("_id") || key === "survey_number") return [label, (r) => r[key], "mono"];
      return [label, (r) => r[key]];
    });
  }

  async function loadRegistry() {
    registryLoaded = true;
    const params = new URLSearchParams({ limit: registry.limit, offset: registry.offset, q: registry.query });
    const table = $("#registry-table");
    try {
      const data = await api(`/api/registries/${registry.table}?${params}`);
      registry.total = data.total || 0;
      const rows = data.rows || [];
      renderTable(table, registryColumns(rows), rows, (row) => openRecord("Registry record", row));
      const first = rows.length ? registry.offset + 1 : 0;
      $("#registry-count").textContent = `Showing ${numberFormat.format(first)}–${numberFormat.format(registry.offset + rows.length)} of ${numberFormat.format(registry.total)} records`;
    } catch (error) {
      registry.total = 0;
      $("thead", table).replaceChildren();
      $("tbody", table).replaceChildren(el("tr", {}, el("td", { class: "empty", text: error.message })));
      $("#registry-count").textContent = "";
    }
    const pages = Math.max(1, Math.ceil(registry.total / registry.limit));
    const page = Math.floor(registry.offset / registry.limit) + 1;
    $("#registry-page").textContent = `Page ${numberFormat.format(page)} of ${numberFormat.format(pages)}`;
    $("#registry-prev").disabled = registry.offset === 0;
    $("#registry-next").disabled = registry.offset + registry.limit >= registry.total;
  }

  function setupRegistry() {
    setupTabs($("#registry-tabs"), (tab) => {
      registry.table = tab.dataset.registry;
      registry.offset = 0;
      loadRegistry();
    });
    $("#registry-search").addEventListener("submit", (event) => {
      event.preventDefault();
      registry.query = $("#registry-q").value.trim();
      registry.offset = 0;
      loadRegistry();
    });
    $("#registry-prev").addEventListener("click", () => {
      registry.offset = Math.max(0, registry.offset - registry.limit);
      loadRegistry();
    });
    $("#registry-next").addEventListener("click", () => {
      registry.offset += registry.limit;
      loadRegistry();
    });
    onEnter.data = () => { if (!registryLoaded) loadRegistry(); };
  }

  // ------------------------------------------------------------ dialog
  function openRecord(title, data) {
    $("#dialog-title").textContent = title;
    $("#dialog-body").textContent = JSON.stringify(data, null, 2);
    const dialog = $("#record-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function setupDialog() {
    const dialog = $("#record-dialog");
    $("#dialog-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close(); // click on the backdrop
    });
  }

  // ------------------------------------------------------------ start
  setupPreferences();
  setupConversation();
  setupTransparency();
  setupRegistry();
  setupDialog();
  onEnter.home = () => { if (health) loadHealth(); };
  setupNavigation();
  loadHealth();
})();
