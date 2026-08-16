/**
 * Sovereign DPI-AI Public Agent — Frontend Application Controller
 * Manages Tab Navigation, Chat & Pipeline Visualizer, Scenario Studio,
 * Real-time Audit Explorers, and SQLite Registry Browser.
 */

// State
let currentAuditType = "consent";
let currentRegistryTable = "identity";
let registryOffset = 0;
const registryLimit = 25;
let cachedAuditRecords = [];

// DOM Loaded
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initChat();
  fetchHealth();
  loadAuditData("consent");
  loadRegistryData("identity");
});

// ---------------------------------------------------------------------------
// Tab Navigation
// ---------------------------------------------------------------------------
function initTabs() {
  const navBtns = document.querySelectorAll(".nav-item");
  navBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      navBtns.forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");

      // Auto load tab data
      if (targetId === "tab-audit") loadAuditData(currentAuditType);
      if (targetId === "tab-registries") loadRegistryData(currentRegistryTable);
    });
  });
}

// ---------------------------------------------------------------------------
// Health & Telemetry
// ---------------------------------------------------------------------------
async function fetchHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();

    // Ollama Status
    const ollamaChip = document.getElementById("chip-ollama");
    const ollamaModel = document.getElementById("val-ollama-model");
    if (data.ollama && data.ollama.available) {
      ollamaModel.textContent = data.config.model || "qwen2.5-coder:7b";
      ollamaChip.querySelector(".status-dot").className = "status-dot dot-green";
    } else {
      ollamaModel.textContent = "Offline (Deterministic Mode)";
      ollamaChip.querySelector(".status-dot").className = "status-dot dot-amber";
    }

    // DB Status
    const dbChip = document.getElementById("chip-db");
    const dbStatus = document.getElementById("val-db-status");
    if (data.database && data.database.initialized) {
      const counts = data.database.tables || {};
      const total = Object.values(counts).reduce((a, b) => a + b, 0);
      dbStatus.textContent = `SQLite (${total.toLocaleString()} rows)`;
    }

    // Policy Threshold
    document.getElementById("val-policy-threshold").textContent = data.config.confidence_threshold || "0.80";
  } catch (err) {
    console.error("Health check failed:", err);
  }
}

// ---------------------------------------------------------------------------
// Chat & Real-Time Pipeline Visualizer
// ---------------------------------------------------------------------------
function initChat() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const toggle = document.getElementById("toggle-live-slm");
  const lblToggle = document.getElementById("lbl-slm-mode");

  toggle.addEventListener("change", () => {
    lblToggle.textContent = toggle.checked ? "Live Ollama SLM" : "Deterministic Mode";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;

    // Add User Bubble
    appendChatBubble("user", query, "Citizen");
    input.value = "";
    input.disabled = true;
    document.getElementById("btn-chat-send").disabled = true;

    // Reset Visual Pipeline
    resetPipeline();
    setPipelineGlobalStatus("Running...", "status-running");

    const useLive = toggle.checked;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, use_live_slm: useLive })
      });
      const result = await res.json();

      // Animate execution steps
      await animatePipelineSteps(result.steps || []);

      // Add Agent Bubble
      let messageText = result.message || "Request processed.";
      if (result.status === "ESCALATED" && result.escalation) {
        messageText = `${result.escalation.message}\nReason: ${result.escalation.reason}`;
      } else if (result.transaction && result.transaction.transaction_id) {
        messageText = `${result.message}\nTransaction ID: ${result.transaction.transaction_id}`;
      }
      appendChatBubble("agent", messageText, "Public Agent", result.status);

      // Global status
      if (result.success) {
        setPipelineGlobalStatus("COMPLETED", "status-pass");
      } else if (result.status === "DENIED_CONSENT") {
        setPipelineGlobalStatus("DENIED", "status-fail");
      } else if (result.status === "ESCALATED") {
        setPipelineGlobalStatus("ESCALATED", "status-warn");
      } else {
        setPipelineGlobalStatus(result.status || "FAILED", "status-fail");
      }

    } catch (err) {
      console.error("Chat error:", err);
      appendChatBubble("agent", `Error processing request: ${err.message}`, "Public Agent", "ERROR");
      setPipelineGlobalStatus("ERROR", "status-fail");
    } finally {
      input.disabled = false;
      document.getElementById("btn-chat-send").disabled = false;
      input.focus();
    }
  });
}

function appendChatBubble(role, text, sender, statusTag) {
  const container = document.getElementById("chat-messages-container");
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble bubble-${role}`;

  const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  let tagHtml = "";
  if (statusTag) {
    const tagClass = getTagClass(statusTag);
    tagHtml = `<span class="table-tag ${tagClass}">${statusTag}</span>`;
  }

  bubble.innerHTML = `
    <div class="bubble-header">
      <span class="bubble-sender">${role === 'user' ? '👤 ' : '🛡️ '}${sender}</span>
      <div>${tagHtml} <span class="bubble-time">${timeStr}</span></div>
    </div>
    <div class="bubble-body">${escapeHtml(text).replace(/\n/g, '<br>')}</div>
  `;

  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function applyPreset(type) {
  const input = document.getElementById("chat-input");
  // Switch to assistant tab
  document.getElementById("nav-btn-assistant").click();

  if (type === "a") {
    input.value = "I am Suresh Kaur, ID 765-741-434. I want to apply for a solar pump subsidy.";
  } else if (type === "b") {
    input.value = "I am Anjali Iyer, ID 213-163-577. I want to assess my credit profile for an agricultural loan.";
  } else if (type === "c") {
    input.value = "I am Suresh Mishra, ID 715-454-455. I need help, I want to apply for a solar pump subsidy.";
  }
  input.focus();
}

// ---------------------------------------------------------------------------
// Pipeline Visualizer Logic
// ---------------------------------------------------------------------------
function resetPipeline() {
  const stages = ["intent", "identity", "consent", "tools", "policy", "risk", "legacy"];
  stages.forEach(s => {
    const node = document.getElementById(`stage-${s}`);
    const badge = document.getElementById(`badge-stage-${s}`);
    if (node) node.className = "stage-node";
    if (badge) {
      badge.className = "stage-status status-idle";
      badge.textContent = "WAITING";
    }
  });
}

function setPipelineGlobalStatus(text, className) {
  const badge = document.getElementById("workflow-status-badge");
  badge.textContent = text;
  badge.className = `pill-badge ${className}`;
}

async function animatePipelineSteps(steps) {
  const streamBody = document.getElementById("live-step-stream");
  streamBody.innerHTML = "";

  for (const step of steps) {
    // Add to stream log
    const entry = document.createElement("div");
    entry.className = `stream-entry entry-${step.status}`;
    entry.innerHTML = `
      <span class="stream-label">[${escapeHtml(step.label)}]</span>
      <span class="stream-msg">${escapeHtml(step.message)} ${step.detail ? `<em>(${escapeHtml(step.detail)})</em>` : ''}</span>
    `;
    streamBody.appendChild(entry);
    streamBody.scrollTop = streamBody.scrollHeight;

    // Highlight corresponding pipeline stage node
    updateStageNodeFromStep(step);

    // Subtle async pacing
    await new Promise(r => setTimeout(r, 140));
  }
}

function updateStageNodeFromStep(step) {
  const label = (step.label || "").toUpperCase();
  let stageKey = null;

  if (label.includes("AGENT")) stageKey = "intent";
  else if (label.includes("WORKFLOW") || label.includes("IDENTITY")) stageKey = "identity";
  else if (label.includes("DEPA")) stageKey = "consent";
  else if (label.includes("AI BLOCK") || label.includes("TOOL")) stageKey = "tools";
  else if (label.includes("POLICY") || label.includes("ELIGIBILITY")) stageKey = "policy";
  else if (label.includes("RISK") || label.includes("NIST")) stageKey = "risk";
  else if (label.includes("LEGACY") || label.includes("AUDIT")) stageKey = "legacy";

  if (stageKey) {
    const node = document.getElementById(`stage-${stageKey}`);
    const badge = document.getElementById(`badge-stage-${stageKey}`);
    const meta = document.getElementById(`meta-${stageKey}`);

    if (node && badge) {
      node.className = `stage-node node-${step.status}`;
      badge.className = `stage-status status-${step.status}`;
      badge.textContent = step.status.toUpperCase();
      if (meta && step.message) meta.textContent = step.message;
    }
  }
}

function clearLiveLog() {
  document.getElementById("live-step-stream").innerHTML = '<div class="stream-empty">Log cleared. Ready for next request.</div>';
}

// ---------------------------------------------------------------------------
// Scenario Studio Runner
// ---------------------------------------------------------------------------
async function runScenario(scenarioId) {
  const outputCard = document.getElementById("scenario-results-card");
  const jsonViewer = document.getElementById("scenario-json-viewer");
  const pill = document.getElementById("scenario-result-pill");

  outputCard.style.display = "block";
  jsonViewer.textContent = `Running Scenario ${scenarioId.toUpperCase()}...`;
  pill.textContent = "Executing...";
  pill.className = "pill-badge status-running";

  try {
    const res = await fetch(`/api/scenario/${scenarioId}`, { method: "POST" });
    const data = await res.json();

    jsonViewer.textContent = JSON.stringify(data, null, 2);
    
    if (data.success) {
      pill.textContent = "COMPLETED (Success)";
      pill.className = "pill-badge status-pass";
    } else if (data.status === "DENIED_CONSENT") {
      pill.textContent = "DENIED (Consent Gate Enforced)";
      pill.className = "pill-badge status-fail";
    } else if (data.status === "ESCALATED") {
      pill.textContent = "ESCALATED (Human Oversight)";
      pill.className = "pill-badge status-warn";
    } else {
      pill.textContent = data.status || "DONE";
      pill.className = "pill-badge";
    }
  } catch (err) {
    jsonViewer.textContent = `Error executing scenario: ${err.message}`;
    pill.textContent = "ERROR";
    pill.className = "pill-badge status-fail";
  }
}

// ---------------------------------------------------------------------------
// Audit Intelligence Center
// ---------------------------------------------------------------------------
function switchAuditSubtab(type) {
  currentAuditType = type;
  document.querySelectorAll("#tab-audit .audit-tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.textContent.toLowerCase().includes(type));
  });
  loadAuditData(type);
}

async function refreshCurrentAudit() {
  loadAuditData(currentAuditType);
}

async function loadAuditData(type) {
  const thead = document.getElementById("audit-table-head");
  const tbody = document.getElementById("audit-table-body");
  tbody.innerHTML = '<tr><td colspan="6" class="table-loading">Fetching immutable audit records...</td></tr>';

  try {
    const res = await fetch(`/api/audit/${type}?limit=100`);
    const data = await res.json();
    cachedAuditRecords = data.records || [];

    renderAuditTable(cachedAuditRecords, type);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="table-loading">Failed to load audit logs: ${err.message}</td></tr>`;
  }
}

function renderAuditTable(records, type) {
  const thead = document.getElementById("audit-table-head");
  const tbody = document.getElementById("audit-table-body");

  if (!records.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="table-loading">No audit records found in log.</td></tr>';
    return;
  }

  if (type === "consent") {
    thead.innerHTML = `
      <tr>
        <th>Timestamp</th>
        <th>Consent ID</th>
        <th>National ID</th>
        <th>Requested Permission</th>
        <th>Result</th>
        <th>Reason</th>
      </tr>
    `;
    tbody.innerHTML = records.map(r => `
      <tr onclick="showDetailModal('Consent Audit Record', ${escapeAttr(JSON.stringify(r))})">
        <td>${formatTime(r.timestamp)}</td>
        <td><code>${escapeHtml(r.consent_id || 'N/A')}</code></td>
        <td><code>${escapeHtml(r.national_id || 'N/A')}</code></td>
        <td><code>${escapeHtml(r.requested_data || 'N/A')}</code></td>
        <td><span class="table-tag ${getTagClass(r.result)}">${escapeHtml(r.result || '?')}</span></td>
        <td>${escapeHtml(r.reason || 'N/A')}</td>
      </tr>
    `).join("");
  } else if (type === "workflow") {
    thead.innerHTML = `
      <tr>
        <th>Timestamp</th>
        <th>Workflow ID</th>
        <th>Intent</th>
        <th>Tools Requested</th>
        <th>Final Status</th>
      </tr>
    `;
    tbody.innerHTML = records.map(r => `
      <tr onclick="showDetailModal('Workflow Audit Record', ${escapeAttr(JSON.stringify(r))})">
        <td>${formatTime(r.timestamp)}</td>
        <td><code>${escapeHtml(r.workflow_id || 'N/A')}</code></td>
        <td><strong>${escapeHtml(r.intent || 'N/A')}</strong></td>
        <td><code>${escapeHtml((r.tools_requested || []).join(', ') || 'None')}</code></td>
        <td><span class="table-tag ${getTagClass(r.final_status)}">${escapeHtml(r.final_status || '?')}</span></td>
      </tr>
    `).join("");
  } else if (type === "escalations") {
    thead.innerHTML = `
      <tr>
        <th>Timestamp</th>
        <th>Case ID</th>
        <th>National ID</th>
        <th>Decision</th>
        <th>Escalation Reason</th>
      </tr>
    `;
    tbody.innerHTML = records.map(r => `
      <tr onclick="showDetailModal('Escalation Case Dossier', ${escapeAttr(JSON.stringify(r))})">
        <td>${formatTime(r.timestamp)}</td>
        <td><code>${escapeHtml(r.case_id || 'N/A')}</code></td>
        <td><code>${escapeHtml(r.national_id || 'N/A')}</code></td>
        <td><span class="table-tag ${getTagClass(r.decision)}">${escapeHtml(r.decision || 'ESCALATED')}</span></td>
        <td>${escapeHtml(r.reason || 'N/A')}</td>
      </tr>
    `).join("");
  }
}

function filterAuditRows() {
  const query = document.getElementById("audit-filter-input").value.toLowerCase();
  if (!query) {
    renderAuditTable(cachedAuditRecords, currentAuditType);
    return;
  }
  const filtered = cachedAuditRecords.filter(r => {
    return JSON.stringify(r).toLowerCase().includes(query);
  });
  renderAuditTable(filtered, currentAuditType);
}

// ---------------------------------------------------------------------------
// DPI Registries Browser
// ---------------------------------------------------------------------------
function switchRegistry(table) {
  currentRegistryTable = table;
  registryOffset = 0;
  document.querySelectorAll("#tab-registries .audit-tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.textContent.toLowerCase().includes(table));
  });
  loadRegistryData(table);
}

async function loadRegistryData(table, search = "") {
  const thead = document.getElementById("registry-table-head");
  const tbody = document.getElementById("registry-table-body");
  const countLbl = document.getElementById("registry-count-label");

  tbody.innerHTML = '<tr><td colspan="7" class="table-loading">Loading registry records from SQLite...</td></tr>';

  try {
    const res = await fetch(`/api/registries/${table}?limit=${registryLimit}&offset=${registryOffset}&q=${encodeURIComponent(search)}`);
    const data = await res.json();
    const rows = data.rows || [];

    countLbl.textContent = `Showing records ${registryOffset + 1} - ${registryOffset + rows.length} of ${data.total.toLocaleString()} total`;

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="table-loading">No records matched query.</td></tr>';
      return;
    }

    const columns = Object.keys(rows[0]);
    thead.innerHTML = `<tr>${columns.map(c => `<th>${escapeHtml(c.replace(/_/g, ' ').toUpperCase())}</th>`).join('')}</tr>`;

    tbody.innerHTML = rows.map(r => `
      <tr onclick="showDetailModal('Registry Record', ${escapeAttr(JSON.stringify(r))})">
        ${columns.map(c => {
          const val = r[c];
          if (c.includes("status")) {
            return `<td><span class="table-tag ${getTagClass(val)}">${escapeHtml(String(val))}</span></td>`;
          }
          if (c.includes("id") || c.includes("signature")) {
            return `<td><code>${escapeHtml(String(val).substring(0, 20))}...</code></td>`;
          }
          return `<td>${escapeHtml(String(val ?? ''))}</td>`;
        }).join('')}
      </tr>
    `).join("");

  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="table-loading">Error loading registry: ${err.message}</td></tr>`;
  }
}

function searchRegistry() {
  const q = document.getElementById("registry-search-input").value.trim();
  registryOffset = 0;
  loadRegistryData(currentRegistryTable, q);
}

function prevRegistryPage() {
  if (registryOffset >= registryLimit) {
    registryOffset -= registryLimit;
    const q = document.getElementById("registry-search-input").value.trim();
    loadRegistryData(currentRegistryTable, q);
  }
}

function nextRegistryPage() {
  registryOffset += registryLimit;
  const q = document.getElementById("registry-search-input").value.trim();
  loadRegistryData(currentRegistryTable, q);
}

// ---------------------------------------------------------------------------
// Detail Modal
// ---------------------------------------------------------------------------
function showDetailModal(title, jsonStr) {
  const modal = document.getElementById("detail-modal");
  const titleElem = document.getElementById("modal-title");
  const bodyElem = document.getElementById("modal-body");

  titleElem.textContent = title;
  const parsed = typeof jsonStr === "string" ? JSON.parse(jsonStr) : jsonStr;
  bodyElem.innerHTML = `<pre class="json-viewer">${escapeHtml(JSON.stringify(parsed, null, 2))}</pre>`;
  modal.style.display = "flex";
}

function closeModal() {
  document.getElementById("detail-modal").style.display = "none";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatTime(isoStr) {
  if (!isoStr) return "";
  return isoStr.substring(0, 19).replace("T", " ");
}

function getTagClass(status) {
  if (!status) return "";
  const s = String(status).toUpperCase();
  if (s.includes("PASS") || s.includes("GRANTED") || s.includes("ACTIVE") || s.includes("COMPLETED")) return "tag-active";
  if (s.includes("FAIL") || s.includes("DENIED") || s.includes("REVOKED") || s.includes("ERROR")) return "tag-denied";
  if (s.includes("WARN") || s.includes("ESCALAT") || s.includes("EXPIRED") || s.includes("SUSPENDED")) return "tag-escalated";
  return "";
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(str) {
  return escapeHtml(str).replace(/"/g, '&quot;');
}
