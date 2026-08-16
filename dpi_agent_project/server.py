"""
Sovereign DPI-AI Public Agent — Web Server & REST API

Provides a local lightweight HTTP and REST API server connecting the
interactive web frontend to the DPI workflow engine, registries, DEPA consent
engine, NIST safeguards, and audit logs.

Endpoints:
    GET  /                     -> Serves web/index.html
    GET  /api/health           -> System, Ollama, database & policy telemetry
    POST /api/chat             -> Process citizen/officer natural language request
    POST /api/scenario/:id     -> Run demonstration scenarios (a, b, c)
    GET  /api/audit/:type      -> Inspect audit logs (consent, workflow, escalations)
    GET  /api/registries/:name -> Query/search simulated SQLite registry data
"""

import json
import logging
import mimetypes
import os
import sqlite3
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    AUDIT_DIRECTORY,
    CONFIDENCE_THRESHOLD,
    CONSENT_AUDIT_LOG,
    DATABASE_PATH,
    ESCALATIONS_LOG,
    HIGH_IMPACT_CONFIDENCE_THRESHOLD,
    LLM_PROVIDER,
    MINIMUM_CREDIT_SCORE,
    MINIMUM_LAND_AREA_ACRES,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    SCENARIO_CITIZENS,
    WORKFLOW_AUDIT_LOG,
)
from dpi_registries import ensure_database_ready
from model_client import health_check
from workflow_engine import (
    process_request,
    run_scenario_a,
    run_scenario_b,
    run_scenario_c,
)

logger = logging.getLogger("web_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WEB_DIR = PROJECT_ROOT / "web"


def _read_jsonl_log(path: Path, limit: int = 100) -> List[Dict[str, Any]]:
    """Read latest N lines from a JSONL log file."""
    if not path.exists():
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        logger.warning("Error reading %s: %s", path, e)
    # Return newest first
    return list(reversed(records[-limit:]))


def _query_registry_table(table_name: str, search: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Query simulated DPI registry tables with search and pagination."""
    table_map = {
        "identity": ("mosip_identity_registry", ["national_id", "full_name", "phone_number", "preferred_language", "identity_status"]),
        "agriculture": ("agriculture_land_registry", ["land_record_id", "national_id", "survey_number", "district", "land_area_acres", "current_crop"]),
        "credit": ("mifos_credit_registry", ["national_id", "credit_score", "avg_monthly_turnover_inr", "repayment_history_status", "active_gst_billing_status"]),
        "consent": ("depa_consent_artefacts", ["consent_id", "national_id", "consent_purpose", "granular_permissions", "consent_status", "valid_until", "cryptographic_signature"]),
        "legacy": ("legacy_municipal_log", ["transaction_id", "national_id", "service_requested", "timestamp", "consent_token_used", "slm_confidence_score", "execution_status"]),
    }
    
    if table_name not in table_map:
        return {"error": f"Unknown registry table '{table_name}'", "rows": [], "total": 0}

    real_table, columns = table_map[table_name]
    
    if not DATABASE_PATH.exists():
        return {"error": "Database not initialized", "rows": [], "total": 0}

    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        if search:
            search_clause = " OR ".join([f"{col} LIKE ?" for col in columns])
            search_params = [f"%{search}%" for _ in columns]
            
            count_sql = f"SELECT COUNT(*) FROM {real_table} WHERE {search_clause}"
            cursor.execute(count_sql, search_params)
            total = cursor.fetchone()[0]

            query_sql = f"SELECT * FROM {real_table} WHERE {search_clause} LIMIT ? OFFSET ?"
            cursor.execute(query_sql, search_params + [limit, offset])
        else:
            cursor.execute(f"SELECT COUNT(*) FROM {real_table}")
            total = cursor.fetchone()[0]
            cursor.execute(f"SELECT * FROM {real_table} LIMIT ? OFFSET ?", (limit, offset))

        rows = [dict(r) for r in cursor.fetchall()]
        
        # Mask phone in identity
        if table_name == "identity":
            for r in rows:
                phone = str(r.get("phone_number", ""))
                r["phone_number"] = "****" + phone[-4:] if len(phone) >= 4 else "****"

        return {"table": table_name, "total": total, "limit": limit, "offset": offset, "rows": rows}
    finally:
        conn.close()


class DPIAgentHTTPHandler(SimpleHTTPRequestHandler):
    """Custom HTTP request handler with REST API and static file serving."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        """Send JSON HTTP response with headers."""
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests for static files and REST API."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # API: /api/health
        if path == "/api/health":
            ollama_health = health_check()
            db_status = {"initialized": DATABASE_PATH.exists()}
            if DATABASE_PATH.exists():
                try:
                    conn = sqlite3.connect(str(DATABASE_PATH))
                    c = conn.cursor()
                    db_status["tables"] = {
                        "identity": c.execute("SELECT COUNT(*) FROM mosip_identity_registry").fetchone()[0],
                        "agriculture": c.execute("SELECT COUNT(*) FROM agriculture_land_registry").fetchone()[0],
                        "credit": c.execute("SELECT COUNT(*) FROM mifos_credit_registry").fetchone()[0],
                        "consent": c.execute("SELECT COUNT(*) FROM depa_consent_artefacts").fetchone()[0],
                        "legacy": c.execute("SELECT COUNT(*) FROM legacy_municipal_log").fetchone()[0],
                    }
                    conn.close()
                except Exception as e:
                    db_status["error"] = str(e)

            response = {
                "system": "Sovereign DPI-AI Public Agent",
                "prototype_status": "Active Local Prototype",
                "ollama": ollama_health,
                "database": db_status,
                "config": {
                    "provider": LLM_PROVIDER,
                    "model": OLLAMA_MODEL,
                    "ollama_host": OLLAMA_HOST,
                    "confidence_threshold": CONFIDENCE_THRESHOLD,
                    "high_impact_confidence_threshold": HIGH_IMPACT_CONFIDENCE_THRESHOLD,
                    "min_land_area_acres": MINIMUM_LAND_AREA_ACRES,
                    "min_credit_score": MINIMUM_CREDIT_SCORE,
                },
                "scenario_citizens": SCENARIO_CITIZENS,
            }
            self._send_json(response)
            return

        # API: /api/audit/:type
        if path.startswith("/api/audit/"):
            log_type = path.replace("/api/audit/", "").strip().lower()
            limit = int(query.get("limit", [50])[0])
            if log_type == "consent":
                self._send_json({"type": "consent", "records": _read_jsonl_log(CONSENT_AUDIT_LOG, limit)})
            elif log_type == "workflow":
                self._send_json({"type": "workflow", "records": _read_jsonl_log(WORKFLOW_AUDIT_LOG, limit)})
            elif log_type == "escalations":
                self._send_json({"type": "escalations", "records": _read_jsonl_log(ESCALATIONS_LOG, limit)})
            else:
                self._send_json({"error": f"Unknown audit type '{log_type}'"}, HTTPStatus.NOT_FOUND)
            return

        # API: /api/registries/:name
        if path.startswith("/api/registries/"):
            name = path.replace("/api/registries/", "").strip().lower()
            search = query.get("q", [""])[0].strip()
            limit = int(query.get("limit", [25])[0])
            offset = int(query.get("offset", [0])[0])
            result = _query_registry_table(name, search=search, limit=limit, offset=offset)
            self._send_json(result)
            return

        # Static file serving
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        """Handle POST requests for chat queries and scenario runs."""
        parsed = urlparse(self.path)
        path = parsed.path

        # Read JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            try:
                raw = self.rfile.read(content_length).decode("utf-8")
                body = json.loads(raw)
            except Exception as e:
                self._send_json({"error": f"Invalid JSON payload: {e}"}, HTTPStatus.BAD_REQUEST)
                return

        # API: /api/chat
        if path == "/api/chat":
            user_input = body.get("query", "").strip()
            if not user_input:
                self._send_json({"error": "Empty 'query' in request body"}, HTTPStatus.BAD_REQUEST)
                return

            use_live_slm = bool(body.get("use_live_slm", False))
            logger.info("Executing chat query (live_slm=%s): %s", use_live_slm, user_input[:80])
            
            try:
                result = process_request(user_input, use_live_slm=use_live_slm)
                self._send_json(result)
            except Exception as e:
                logger.error("Error processing request: %s", e, exc_info=True)
                self._send_json({"success": False, "status": "ERROR", "error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # API: /api/scenario/:id
        if path.startswith("/api/scenario/"):
            scenario_id = path.replace("/api/scenario/", "").strip().lower()
            logger.info("Executing Scenario: %s", scenario_id)
            if scenario_id == "a":
                result = run_scenario_a()
            elif scenario_id == "b":
                result = run_scenario_b()
            elif scenario_id == "c":
                result = run_scenario_c()
            else:
                self._send_json({"error": f"Unknown scenario ID '{scenario_id}' (use a, b, or c)"}, HTTPStatus.NOT_FOUND)
                return

            self._send_json(result)
            return

        self._send_json({"error": f"Route not found: {path}"}, HTTPStatus.NOT_FOUND)


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Initialize database and start the web server."""
    ensure_database_ready()
    AUDIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    WEB_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((host, port), DPIAgentHTTPHandler)
    url = f"http://{host}:{port}"
    print(f"\n========================================================")
    print(f" SOVEREIGN DPI-AI PUBLIC AGENT — LOCAL WEB INTERFACE")
    print(f"========================================================")
    print(f"  * Local Web App: {url}")
    print(f"  * Ollama Host:   {OLLAMA_HOST} (Model: {OLLAMA_MODEL})")
    print(f"  * Database:      {DATABASE_PATH}")
    print(f"========================================================\n")
    logger.info("Server started on %s", url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web server...")
        server.server_close()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    start_server(port=port)
