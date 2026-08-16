# Project Report: Sovereign DPI-AI Public Agent Prototype

**Candidate / Engineer:** AI Systems & Governance Engineer  
**Project Name:** Sovereign DPI-AI Public Agent  
**Domain:** Digital Public Infrastructure (DPI), Data Governance, Responsible AI & Local SLM Architecture  
**Status:** Completed & Validated Working Prototype (Local On-Device Execution)

---

## Executive Summary

The **Sovereign DPI-AI Public Agent** is a full-stack architectural prototype demonstrating how **Digital Public Infrastructure (DPI)** can be integrated with **Small Language Models (SLMs)** while enforcing strict **data sovereignty, consent-based access, deterministic governance, and NIST AI RMF-inspired safeguards**.

Built entirely for local, air-gapped execution on edge devices (using Python 3.11+, Ollama with `qwen2.5-coder:7b`, SQLite, and local datasets), the prototype operationalizes the core security principle: **"The Model Proposes, The Deterministic Workflow Decides."** The SLM is strictly constrained to proposing structured JSON action plans and is mathematically and architecturally prohibited from executing code, SQL queries, or unauthorized API calls.

---

## Interactive Public Agent Web Interface

![Sovereign DPI-AI Public Agent Interactive Web Interface](./web_interface_preview.png)

*Figure 1: The Sovereign DPI-AI Public Agent Web Studio featuring the live Small Language Model conversational assistant (left) and the real-time 7-stage deterministic DPI governance & safeguard pipeline visualizer (right).*

---

## Conceptual & Regulatory Foundations

The architecture implements concepts and terminology from leading international and national governance frameworks:

1. **CDPI DPI-AI Framework 2026 & AI × DPI Policy:** Implements modular **AI Blocks**, **DPI Workflows**, and the **"+1 Approach"** to legacy municipal writeback.
2. **India DEPA Framework (Data Empowerment and Protection Architecture):** Implements a **5-gate deterministic consent engine** guarding protected citizen data.
3. **NIST AI RMF 1.0 (Artificial Intelligence Risk Management Framework):** Implements core risk functions (**GOVERN, MAP, MEASURE, MANAGE**) to validate model confidence, detect ambiguity, filter bias/risk indicators, and trigger automated human escalation dossiers.

---

## System Architecture & Security Boundaries

```
[ Citizen / Extension Officer ]
              │ (Natural Language via Web UI or Terminal)
              ▼
[ Local SLM: qwen2.5-coder:7b ] ──► Emits Structured JSON Plan ONLY
              │
              ▼
╔═════════════════════════════════════════════════════════════════════╗
║                   DETERMINISTIC WORKFLOW ENGINE                     ║
╠══════════════════════╦══════════════════════╦═══════════════════════╣
║ 1. Schema Validation ║ 2. Identity Check    ║ 3. DEPA Consent Gate  ║
║ (Strict JSON types)  ║ (MOSIP - Masked PII) ║ (5-Gate Verification) ║
╠══════════════════════╬══════════════════════╬═══════════════════════╣
║ 4. MCP Tool Router   ║ 5. Policy Engine     ║ 6. NIST Risk Gate     ║
║ (Allowlist execution)║ (Deterministic rules)║ (Confidence & Bias)   ║
╚══════════════════════╩══════════════════════╩═══════════════════════╝
              │
      ┌───────┴───────────────────────┐
      ▼ (If Passed)                   ▼ (If Low Confidence / High Risk)
[ +1 Legacy Adapter & DB ]      [ Human Review Escalation Dossier ]
      │                               │
      └───────────────┬───────────────┘
                      ▼
[ Immutable Audit Logs (JSONL) ] ──► [ User Response (Preferred Language) ]
```

### Key Engineering Safeguards:
- **No Arbitrary Execution:** Zero arbitrary code execution; all AI Blocks are pre-defined, typed, and isolated.
- **Privacy by Design:** Phone numbers and sensitive identifiers are masked; raw protected datasets (credit/land) are inaccessible without explicit, active, granular consent.
- **Full Auditability:** Every consent evaluation, workflow transition, and human escalation creates an immutable, timestamped JSONL audit log.

---

## Core Technical Highlights

| Component | Implementation Details | Key Highlights |
|---|---|---|
| **Local SLM Client** | `model_client.py` | Pluggable provider architecture (`OllamaProvider`, `CloudProvider` stub, `DeterministicProvider`). Temperature 0.1 for high reproducibility. |
| **DEPA Consent Engine** | `depa_consent.py` | Evaluates Status (`ACTIVE`), Expiry (UTC timestamp), Granularity (`agri_land:read`, `mifos_credit:read`), Purpose alignment, and SHA-256 signature integrity. |
| **MCP Tool Router** | `mcp_tools.py` | Strict argument schema validation and tool allowlisting for `verify_identity`, `fetch_land_record`, `assess_credit_profile`, `evaluate_subsidy_eligibility`, etc. |
| **NIST Safeguards** | `nist_safeguards.py` | Confidence evaluation against application prototype threshold (0.80), ambiguity detector, high-impact classifier, and prototype risk-indicator filter. |
| **+1 Legacy Adapter** | `legacy_adapter.py` | Non-invasive writeback appending to SQLite and separate output CSVs without corrupting foundational registries. |
| **Dual Interfaces** | `app.py` & `server.py` + `web/` | Interactive CLI menu + Modern Glassmorphic Web Application (`http://localhost:8000`) with real-time 7-stage pipeline animation and registry browser. |

---

## Demonstration Scenarios & Test Verification

The system was evaluated against **79 automated unit and integration tests (100% pass rate)** and demonstrated across three real-world scenarios using actual 50,000-record datasets:

1. **Scenario A — Solar Pump Subsidy (Approved End-to-End):**
   - *Citizen:* Suresh Kaur (`765-741-434`)
   - *Flow:* Identity (ACTIVE) $\rightarrow$ DEPA Consent (`CON-100007`, `agri_land:read` PASS) $\rightarrow$ Land Record (12.8 acres) $\rightarrow$ Policy ($\ge 2.0$ ac PASS) $\rightarrow$ NIST Gate (Confidence 1.0 $\ge 0.80$ PASS) $\rightarrow$ Legacy Transaction (`TXN-DF7BC44B`) created.
2. **Scenario B — Credit Assessment (DEPA Consent Failure):**
   - *Citizen:* Anjali Iyer (`213-163-577`)
   - *Flow:* Identity (ACTIVE) $\rightarrow$ DEPA Consent evaluated for credit $\rightarrow$ **DENIED** (`GRANULAR_PERMISSION_MISSING: mifos_credit`). Credit registry was **never accessed** (zero data leakage).
3. **Scenario C — Human Escalation (Safety & Oversight Gate):**
   - *Citizen:* Suresh Mishra (`715-454-455`)
   - *Flow:* Identity status is `SUSPENDED` $\rightarrow$ Automatic processing blocked $\rightarrow$ Case dossier (`ESC-28f4d00f-ebc`) logged in `audit/escalations_log.jsonl` $\rightarrow$ Transparent human-review notice returned.

---

## Value Proposition for Hiring Teams

This project demonstrates practical competency in:
- **Operationalizing Responsible AI & AI Governance:** Moving beyond theoretical compliance into deterministic policy engines, algorithmic guardrails, and audit trails.
- **Architecting Public-Sector / Enterprise AI:** Designing resilient systems that respect data sovereignty, consent frameworks (DEPA), and risk management standards (NIST AI RMF).
- **Production-Quality Systems Engineering:** Clean, modular Python code (zero bloat), comprehensive unit/integration test coverage, defense-in-depth security, and modern visual UI delivery.

---

*Project codebase, documentation, and live test suite available upon request.*
