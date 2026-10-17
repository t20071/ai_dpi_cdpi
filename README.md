# AI + DPI: Sovereign DPI-AI Public Agent

[![CI](https://github.com/t20071/ai_dpi_cdpi/actions/workflows/ci.yml/badge.svg)](https://github.com/t20071/ai_dpi_cdpi/actions/workflows/ci.yml)

The project is in **[`dpi_agent_project/`](dpi_agent_project/)**. Its
[README](dpi_agent_project/README.md) explains how it works, how to run it locally and how to
deploy it to Vercel.

An AI public agent built on Digital Public Infrastructure (DPI). A language model (a local
model in Ollama, or a cloud model through Groq) interprets a citizen's request, and a
deterministic workflow decides. It checks identity, DEPA-style consent, eligibility and a
NIST AI RMF-inspired risk gate, sends uncertain cases to a human officer, and keeps a
tamper-evident audit log.

![Home page](dpi_agent_project/screenshots/01-home.png)

## Quick start (Windows PowerShell)

```powershell
git clone https://github.com/t20071/ai_dpi_cdpi.git
cd ai_dpi_cdpi\dpi_agent_project
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env      # optional: add GROQ_API_KEY
dpi-agent serve             # http://127.0.0.1:8000
```

## Repository layout

| Path | What it is |
|------|------------|
| [`dpi_agent_project/`](dpi_agent_project/) | The whole project: code, website, data, tests and docs |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | CI. It stays at the root because GitHub only reads workflows from here |

> This is a prototype with synthetic data, not an official government system.
