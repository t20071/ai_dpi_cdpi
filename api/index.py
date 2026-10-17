"""
Vercel entry point.

Vercel runs this file as a Python function for every /api/* request (see the
rewrite in vercel.json).  The website itself is served from public/ by
Vercel's CDN.  Locally you do not need this file: use `dpi-agent serve`.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Import the app from the source tree, so its data/ folder is found.
sys.path.insert(0, str(ROOT / "src"))

# Vercel's file system is read-only except /tmp.  The database is rebuilt
# from data/raw/ on each cold start, and audit logs last for one instance.
os.environ.setdefault("DPI_RUNTIME_DIR", "/tmp/dpi-agent")

# There is no Ollama in the cloud: default to Groq (set GROQ_API_KEY).
os.environ.setdefault("LLM_PROVIDER", "groq")

from dpi_agent.web.wsgi import app  # noqa: E402

__all__ = ["app"]
