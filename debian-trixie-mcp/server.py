#!/usr/bin/env python3
"""Kompletny Serwer Model Context Protocol (MCP) dla Debiana 13 "Trixie".

Obsługuje:
- Transporty: stdio (domyślny) oraz streamable-http (SSE / Starlette) na 127.0.0.1:8765
- Autoryzację tokenem Bearer z .env (porównywaną przez hmac.compare_digest)
- Dynamiczną architekturę wtyczek (katalog plugins/, włączanie przez MCP_PLUGINS)
- Ścisły podział na logi (wyłącznie stderr w trybie stdio) i protokół MCP
"""
from __future__ import annotations

import argparse
import hmac
import importlib.util
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

# Wczytywanie konfiguracji z pliku .env (chmod 600)
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

# Konfiguracja logowania wyłącznie na sys.stderr, aby nie zanieczyszczać stdout protokołu MCP
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("debian13_mcp")


def get_workspace() -> Path:
    ws_env = os.environ.get("MCP_WORKSPACE", "").strip()
    if ws_env:
        ws = Path(ws_env).expanduser().resolve()
    else:
        ws = BASE_DIR.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def safe_run(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
    """Bezpieczne uruchamianie procesu bez powłoki (shell=False)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or get_workspace(),
            shell=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"Błąd: Przekroczono limit czasu ({timeout}s)."
    except FileNotFoundError:
        return -1, "", f"Błąd: Nie znaleziono pliku wykonywalnego '{cmd[0]}'."
    except Exception as exc:
        return -1, "", f"Wyjątek wykonania: {exc}"


def clip_output(text: str, max_chars: int = 25000) -> str:
    """Przycina zbyt długie wyjście tekstowe z informacją dla modelu."""
    if len(text) <= max_chars:
        return text
    skipped = len(text) - max_chars
    return text[:max_chars] + f"\n\n... [Przycięto: wynik przekracza {max_chars} znaków, pominięto {skipped} znaków]"


def load_plugins(mcp, workspace: Path):
    """Dynamicznie ładuje wtyczki z katalogu plugins/ na podstawie zmiennej MCP_PLUGINS."""
    plugins_dir = BASE_DIR / "plugins"
    if not plugins_dir.exists():
        logger.warning("Katalog wtyczek '%s' nie istnieje.", plugins_dir)
        return

    requested = os.environ.get("MCP_PLUGINS", "*").strip()
    if requested == "*":
        plugin_names = [p.stem for p in plugins_dir.glob("*.py") if p.stem != "__init__"]
    else:
        plugin_names = [p.strip() for p in requested.split(",") if p.strip()]

    loaded_count = 0
    for name in plugin_names:
        plugin_file = plugins_dir / f"{name}.py"
        if not plugin_file.exists():
            logger.warning("Nie znaleziono pliku wtyczki: %s", plugin_file)
            continue

        try:
            spec = importlib.util.spec_from_file_location(f"debian13_mcp.plugins.{name}", plugin_file)
            if not spec or not spec.loader:
                logger.error("Nie udało się utworzyć specyfikacji modułu dla: %s", name)
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "register") and callable(module.register):
                module.register(mcp, safe_run, clip_output, workspace)
                loaded_count += 1
                logger.info("Załadowano wtyczkę: %s", name)
            else:
                logger.warning("Wtyczka '%s' nie zawiera wymaganej funkcji register().", name)
        except Exception as exc:
            logger.error("Błąd podczas ładowania wtyczki '%s': %s", name, exc, exc_info=True)

    logger.info("Łącznie załadowano %d wtyczek MCP.", loaded_count)


def create_mcp_server(workspace: Path):
    """Inicjalizuje serwer FastMCP z biblioteki mcp."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        logger.critical(
            "Brak biblioteki 'mcp'. Upewnij się, że serwer działa w .venv (uruchom: ./install.sh)."
        )
        sys.exit(1)

    mcp = FastMCP(
        "Debian 13 Trixie Server",
        instructions="Kompletny serwer MCP dla Debiana 13 (Trixie). Udostępnia narzędzia systemowe, plikowe, git, kontenery i root.",
    )

    load_plugins(mcp, workspace)
    return mcp


def run_http_server(mcp, host: str, port: int, expected_token: str):
    """Uruchamia serwer w trybie streamable-http / SSE z autoryzacją Bearer Token."""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        def __init__(self, app, token: str):
            super().__init__(app)
            self.expected = token

        async def dispatch(self, request, call_next):
            # Endpointy diagnostyczne lub favicon można przepuścić opcjonalnie
            if request.url.path in ("/healthz", "/ping"):
                return JSONResponse({"status": "ok", "system": "debian-13-trixie-mcp"})

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    {"error": "Brak lub nieprawidłowy nagłówek autoryzacji. Wymagany: Authorization: Bearer <token>"},
                    status_code=401,
                )

            provided = auth_header[7:].strip()
            # Stałoczasowe porównanie tokenu (ochrona przed atakiem Timing Attack)
            if not hmac.compare_digest(provided, self.expected):
                return JSONResponse({"error": "Odmowa dostępu: Nieprawidłowy Bearer Token."}, status_code=403)

            return await call_next(request)

    logger.info("Przygotowywanie aplikacji HTTP / SSE...")
    app = mcp.sse_app()
    app.add_middleware(BearerAuthMiddleware, token=expected_token)

    logger.info("Uruchamianie serwera MCP na http://%s:%d z autoryzacją Bearer...", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description="Debian 13 Trixie MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport komunikacji MCP (stdio lub http/sse, domyślnie stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "127.0.0.1"),
        help="Adres IP nasłuchu HTTP (domyślnie 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "8765")),
        help="Port nasłuchu HTTP (domyślnie 8765)",
    )
    args = parser.parse_args()

    workspace = get_workspace()
    logger.info("Katalog roboczy MCP: %s", workspace)

    mcp = create_mcp_server(workspace)

    if args.transport in ("http", "sse"):
        token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
        if not token or "zastap_tym" in token:
            logger.critical(
                "BŁĄD KRYTYCZNY: Brak skonfigurowanego bezpiecznego tokenu MCP_BEARER_TOKEN w pliku .env!\n"
                "W trybie HTTP/SSE serwer kategorycznie odmawia startu bez uwierzytelnienia.\n"
                "Wygeneruj token (np. openssl rand -hex 32) i wpisz go do .env."
            )
            sys.exit(1)

        run_http_server(mcp, args.host, args.port, token)
    else:
        logger.info("Uruchamianie serwera MCP w trybie transportu STDIO...")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
