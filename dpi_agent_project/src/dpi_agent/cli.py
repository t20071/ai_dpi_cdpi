"""
Command-line interface:  dpi-agent <command>   (or: python -m dpi_agent <command>)

    (no command)     interactive menu
    serve            start the web UI at http://127.0.0.1:8000
    ask "TEXT"       run one request through the full workflow
    scenario a|b|c   run a demonstration scenario (or "all")
    models           list local (Ollama) and cloud (Groq) models
    eval             score models on evals/intent_cases.jsonl
    audit verify     check the audit logs have not been tampered with
    audit show TYPE  print recent audit records (consent|workflow|escalations)
    init-db          import the CSV datasets into SQLite (--rebuild to redo)

Choose the interpreter on any command with --provider ollama|groq|deterministic
(or --offline for deterministic) and --model NAME.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dpi_agent import __version__, config
from dpi_agent.audit import ALL_LOGS
from dpi_agent.llm.providers import (
    DeterministicProvider,
    GroqProvider,
    LLMProvider,
    LLMUnavailable,
    OllamaProvider,
    choose_model,
    get_provider,
    list_ollama_models,
)
from dpi_agent.registries.database import ensure_database_ready, rebuild_database

_MARKS = {"pass": "+", "fail": "x", "warn": "!", "info": "-"}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_step(step: dict[str, Any]) -> None:
    mark = _MARKS.get(step["status"], "-")
    line = f"  {mark} [{step['label']}] {step['message']}"
    if step.get("detail"):
        line += f"  ({step['detail']})"
    print(line)


def _print_result(result: dict[str, Any]) -> None:
    print("  " + "-" * 60)
    print(f"  RESULT: {result.get('status', 'UNKNOWN')}")
    if result.get("message"):
        print(f"  {result['message']}")
    if result.get("escalation"):
        print(f"  {result['escalation'].get('message', '')}")
        print(f"  Reason: {result['escalation'].get('reason', '')}")
    if result.get("errors"):
        for error in result["errors"]:
            print(f"  x {error}")
    if result.get("error"):
        print(f"  Error: {result['error']}")
    if result.get("interpreter"):
        print(f"  Interpreted by: {result['interpreter']}")
    print("  " + "-" * 60)


def _provider_name(args: argparse.Namespace) -> str:
    """The provider chosen by --offline / --provider, else LLM_PROVIDER."""
    if getattr(args, "offline", False):
        return "deterministic"
    return (getattr(args, "provider", None) or config.LLM_PROVIDER).lower()


def _live_provider(args: argparse.Namespace) -> LLMProvider:
    """The provider selected by --offline / --provider / --model / LLM_PROVIDER."""
    return get_provider(_provider_name(args), getattr(args, "model", None))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from dpi_agent.web.api import providers
    from dpi_agent.web.server import create_server

    name = _provider_name(args)
    providers.use(name, _live_provider(args))
    server = create_server(args.host, args.port)
    print("=" * 64)
    print("  SOVEREIGN DPI-AI PUBLIC AGENT - local web interface")
    print("=" * 64)
    print(f"  Web UI:    http://{args.host}:{args.port}")
    for provider_name, health in providers.health().items():
        if health.get("available"):
            state = health.get("model") or "ready"
        else:
            state = f"not available ({health.get('error', '')})"
        default = "  <- default" if provider_name == name else ""
        print(f"  {provider_name + ':':<14} {state}{default}")
    print(f"  Database:  {config.DATABASE_PATH}")
    print("  Press Ctrl+C to stop.")
    print("=" * 64, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web server...")
    finally:
        server.server_close()
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from dpi_agent.workflows.engine import process_request

    ensure_database_ready()
    provider = _live_provider(args)
    print(f"\n  Citizen > {args.text}\n")
    result = process_request(
        args.text, provider=provider, on_step=None if args.json else _print_step
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    else:
        _print_result(result)
    return 0 if result.get("success") else 1


def cmd_scenario(args: argparse.Namespace) -> int:
    from dpi_agent.workflows.scenarios import SCENARIOS, run_scenario

    ensure_database_ready()
    keys = list(SCENARIOS) if args.name == "all" else [args.name]
    for key in keys:
        scenario = SCENARIOS[key]
        print("=" * 64)
        print(f"  SCENARIO {key.upper()}: {scenario.title}")
        print(f"  Citizen: {scenario.citizen} (ID {scenario.national_id})")
        print(f"  {scenario.description}")
        print("=" * 64)
        print(f"  Citizen > {scenario.request}\n")
        _print_result(run_scenario(key, on_step=_print_step))
        print()
    return 0


def _print_ollama_models(requested: str | None) -> bool:
    print(f"  LOCAL - Ollama at {config.OLLAMA_HOST} (OLLAMA_MODEL={config.OLLAMA_MODEL})")
    try:
        installed = list_ollama_models()
    except LLMUnavailable as exc:
        print(f"  ! {exc}")
        return False
    if not installed:
        print("  No models installed. Install one, e.g.:  ollama pull llama3.2:3b")
        return False
    try:
        selected = choose_model(requested or config.OLLAMA_MODEL, installed)
    except LLMUnavailable as exc:
        selected = None
        print(f"  ! {exc}")
    print(f"  {'':2}{'MODEL':<32}{'PARAMS':>8}{'SIZE':>10}  FAMILY")
    for m in sorted(installed, key=lambda m: -m.parameters):
        mark = "*" if m.name == selected else ("e" if m.is_embedding else " ")
        size = f"{m.size_bytes / 1e9:.1f} GB"
        print(f"  {mark} {m.name:<32}{m.parameter_size:>8}{size:>10}  {m.family}")
    return True


def _print_groq_models() -> bool:
    print(f"  CLOUD - Groq (GROQ_MODEL={config.GROQ_MODEL})")
    if not config.GROQ_API_KEY:
        print("  ! GROQ_API_KEY is not set. Add it to .env to use Groq.")
        return False
    try:
        models = GroqProvider().list_models()
    except LLMUnavailable as exc:
        print(f"  ! {exc}")
        return False
    for model in models:
        print(f"  {'*' if model == config.GROQ_MODEL else ' '} {model}")
    return True


def cmd_models(args: argparse.Namespace) -> int:
    local_ok = _print_ollama_models(getattr(args, "model", None))
    print()
    cloud_ok = _print_groq_models()
    print("\n  * = used by the agent   e = embedding model (not usable for chat)")
    print(f"  Default provider: LLM_PROVIDER={config.LLM_PROVIDER}")
    print("  Change models with OLLAMA_MODEL / GROQ_MODEL in .env, or --model NAME on a command.")
    return 0 if local_ok or cloud_ok else 1


def cmd_eval(args: argparse.Namespace) -> int:
    from dpi_agent.evaluation import evaluate, format_comparison, load_cases, save_report

    cases = load_cases(Path(args.cases))
    providers: list[LLMProvider] = [DeterministicProvider()]
    name = _provider_name(args)
    if name == "ollama":
        try:
            installed = [m for m in list_ollama_models() if not m.is_embedding]
        except LLMUnavailable as exc:
            print(f"  ! {exc} Evaluating the deterministic rules only.\n")
            installed = []
        if args.all_models:
            names = [m.name for m in installed]
        elif args.eval_models:
            names = args.eval_models
        else:
            names = [choose_model(config.OLLAMA_MODEL, installed)] if installed else []
        providers += [OllamaProvider(model=model) for model in names]
    elif name == "groq":
        if not config.GROQ_API_KEY:
            print("  ! GROQ_API_KEY is not set. Evaluating the deterministic rules only.\n")
        else:
            if args.all_models:
                names = GroqProvider().list_models()
            else:
                names = args.eval_models or [config.GROQ_MODEL]
            providers += [GroqProvider(model=model) for model in names]

    reports = []
    for provider in providers:
        print(f"  Evaluating {provider.name} on {len(cases)} cases ", end="", flush=True)
        report = evaluate(provider, cases, on_case=lambda r: print(".", end="", flush=True))
        path = save_report(report)
        print(f" done  ->  {path}")
        reports.append(report)
        if args.verbose_cases:
            for r in report["results"]:
                ok = r["outcome"] == r["expected_outcome"]
                print(
                    f"    {'ok  ' if ok else 'MISS'} {r['id']:<22} expected {r['expected_outcome']:<7} "
                    f"got {r['outcome']:<7} model intent: {r['model_intent']}"
                )

    print("\n" + format_comparison(reports) + "\n")
    print("  unsafe = would have started a workflow it should not have (must be 0)")
    print("  over-held = sent to a human although it could have proceeded\n")
    return 1 if any(r["summary"]["unsafe"] for r in reports) else 0


def cmd_audit(args: argparse.Namespace) -> int:
    if args.action == "verify":
        all_ok = True
        for name, log in ALL_LOGS.items():
            result = log.verify()
            if result.ok:
                print(f"  + {name:<12} {result.records:>5} records, chain intact")
            else:
                all_ok = False
                print(f"  x {name:<12} TAMPERED at line {result.line}: {result.error}")
        return 0 if all_ok else 1

    log = ALL_LOGS[args.log_type]
    records = log.read(args.limit)
    if not records:
        print(f"  (no {args.log_type} records yet)")
    for record in reversed(records):
        print(json.dumps(record, ensure_ascii=False, default=str))
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    counts = rebuild_database() if args.rebuild else ensure_database_ready()
    imported = sum(counts.values())
    print(f"  Database: {config.DATABASE_PATH}")
    if imported:
        for table, count in counts.items():
            print(f"    {table:<28} {count:>6,} rows imported")
    else:
        print("  Already initialized (use --rebuild to re-import from data/raw/).")
    return 0


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------

_MENU = """
  1. Run Scenario A - Subsidy Success
  2. Run Scenario B - DEPA Consent Failure
  3. Run Scenario C - Human Escalation
  4. Talk to the Public Agent
  5. Show consent audit log
  6. Show workflow audit log
  7. Show escalations
  8. List local models
  9. Verify audit log integrity
  0. Exit
"""


def _chat_loop(provider: LLMProvider) -> None:
    from dpi_agent.workflows.engine import process_request

    health = provider.health()
    if not health.get("available"):
        print(f"  ! Model unavailable ({health.get('error')}). Using deterministic rules.")
        provider = DeterministicProvider()
    print(f"  Interpreter: {provider.name}")
    print("  Type a request (include your ID, e.g. 765-741-434). Empty line or 'exit' to return.\n")
    while True:
        try:
            text = input("  Citizen > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text or text.lower() in {"exit", "quit"}:
            return
        _print_result(process_request(text, provider=provider, on_step=_print_step))
        print()


def cmd_menu(args: argparse.Namespace) -> int:
    print("=" * 64)
    print("  SOVEREIGN DPI-AI PUBLIC AGENT")
    print("  Local prototype - NOT a production government system.")
    print("  All data and inference stay on this machine.")
    print("=" * 64)
    counts = ensure_database_ready()
    if sum(counts.values()):
        print(f"  Imported {sum(counts.values()):,} records into {config.DATABASE_PATH}")
    provider = _live_provider(args)

    actions = {
        "1": lambda: cmd_scenario(argparse.Namespace(name="a")),
        "2": lambda: cmd_scenario(argparse.Namespace(name="b")),
        "3": lambda: cmd_scenario(argparse.Namespace(name="c")),
        "4": lambda: _chat_loop(provider),
        "5": lambda: cmd_audit(argparse.Namespace(action="show", log_type="consent", limit=15)),
        "6": lambda: cmd_audit(argparse.Namespace(action="show", log_type="workflow", limit=15)),
        "7": lambda: cmd_audit(argparse.Namespace(action="show", log_type="escalations", limit=15)),
        "8": lambda: cmd_models(argparse.Namespace(model=getattr(args, "model", None))),
        "9": lambda: cmd_audit(argparse.Namespace(action="verify")),
    }
    while True:
        print(_MENU)
        try:
            choice = input("  Select option > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "0":
            return 0
        action = actions.get(choice)
        if action:
            action()
        else:
            print("  Please choose 0-9.")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _add_model_options(parser: argparse.ArgumentParser, top_level: bool = False) -> None:
    # Sub-commands use SUPPRESS so they do not overwrite values given before
    # the command name (argparse copies sub-parser defaults over the parent's).
    default = None if top_level else argparse.SUPPRESS
    parser.add_argument(
        "--provider",
        choices=config.PROVIDERS,
        default=default,
        help="interpreter to use (overrides LLM_PROVIDER)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--model",
        default=default,
        help="model for that provider (overrides OLLAMA_MODEL / GROQ_MODEL)",
    )
    group.add_argument(
        "--offline",
        action="store_true",
        default=False if top_level else argparse.SUPPRESS,
        help="use the deterministic rules, no language model",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dpi-agent",
        description="Sovereign DPI-AI Public Agent - local prototype.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="more logging (-vv for debug)"
    )
    _add_model_options(parser, top_level=True)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="start the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    _add_model_options(p)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ask", help="run one request through the workflow")
    p.add_argument("text", help='the citizen\'s request, e.g. "ID 765-741-434, solar pump subsidy"')
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    _add_model_options(p)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("scenario", help="run a demonstration scenario")
    p.add_argument("name", choices=["a", "b", "c", "all"])
    p.set_defaults(func=cmd_scenario)

    p = sub.add_parser("models", help="list installed Ollama models")
    p.add_argument(
        "--model", default=argparse.SUPPRESS, help="show which model this name resolves to"
    )
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("eval", help="evaluate models on the labelled request set")
    p.add_argument(
        "--model", action="append", dest="eval_models", help="model to evaluate (repeatable)"
    )
    p.add_argument(
        "--all-models", action="store_true", help="evaluate every chat model of that provider"
    )
    p.add_argument(
        "--provider",
        choices=config.PROVIDERS,
        default=argparse.SUPPRESS,
        help="whose models to evaluate (default: LLM_PROVIDER)",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        default=argparse.SUPPRESS,
        help="evaluate the deterministic rules only",
    )
    p.add_argument("--cases", default=str(config.REPO_ROOT / "evals" / "intent_cases.jsonl"))
    p.add_argument("--verbose-cases", action="store_true", help="print every case result")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("audit", help="inspect or verify audit logs")
    audit_sub = p.add_subparsers(dest="action", required=True)
    audit_sub.add_parser("verify", help="check every log's hash chain")
    show = audit_sub.add_parser("show", help="print recent records")
    show.add_argument("log_type", choices=list(ALL_LOGS))
    show.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("init-db", help="import the CSV datasets into SQLite")
    p.add_argument("--rebuild", action="store_true", help="delete and re-import the database")
    p.set_defaults(func=cmd_init_db)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Hindi/Tamil text must survive pipes and redirects.  On Windows these
    # default to the ANSI code page (e.g. cp1252), which garbles Devanagari;
    # an interactive console already uses Unicode and is left alone.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    level = [logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args) if hasattr(args, "func") else cmd_menu(args)
