import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv(
    "DEBIAN13_API_BASE",
    "http://127.0.0.1:8765"
).rstrip("/")

TOKEN = os.getenv("DEBIAN13_API_TOKEN", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(path, payload=None, authenticated=True):
    headers = {
        "Accept": (
            "application/json, text/event-stream"
            if payload is not None
            else "application/json"
        )
    }

    if authenticated:
        headers["Authorization"] = f"Bearer {TOKEN}"

    data = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers=headers,
        method="POST" if payload is not None else "GET"
    )

    opener = urllib.request.build_opener(NoRedirect)

    try:
        with opener.open(req, timeout=8) as response:
            return (
                response.status,
                json.loads(response.read(65536))
            )
    except urllib.error.HTTPError as error:
        return error.code, {}


def main():
    parsed = urllib.parse.urlsplit(BASE)

    local = parsed.hostname in (
        "127.0.0.1", "localhost", "::1"
    )

    if (
        parsed.scheme != "https"
        and not (local and parsed.scheme == "http")
    ):
        raise RuntimeError("Wymagany bezpieczny HTTPS")

    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise RuntimeError("Nieprawidłowy adres API")

    if len(TOKEN) < 32:
        raise RuntimeError("Brak tokenu operatora")

    status, data = request(
        "/healthz",
        authenticated=False
    )
    assert status == 200
    assert data.get("status") == "ok"
    print("OK: Serwer Python")

    status, _ = request(
        "/v1/diagnostics",
        authenticated=False
    )
    assert status == 401
    print("OK: Ochrona API")

    status, data = request("/v1/diagnostics")
    assert status == 200
    assert data.get("service") == "debian13-system"
    print("OK: Diagnostyka API")

    status, data = request("/mcp", {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {
                "name": "AI-Studio-Debian13",
                "version": "1.0"
            }
        }
    })

    assert status == 200
    assert "result" in data
    print("OK: MCP initialize")

    status, data = request("/mcp", {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    })

    assert status == 200
    tools = data["result"]["tools"]
    assert isinstance(tools, list)
    print("OK: MCP tools/list")

    status, data = request("/mcp", {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "debian13_diagnostics",
            "arguments": {}
        }
    })

    assert status == 200
    assert data["result"].get("isError") is False
    print("OK: MCP diagnostics")

    print("PYTHON MCP: TESTY LOKALNE ZALICZONE")
    print(
        "Uwaga: to nie potwierdza jeszcze "
        "podłączenia klienta ChatGPT."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "BŁĄD: sprawdź serwer, endpointy "
            "i autoryzację; nie ujawniaj tokenów.",
            file=sys.stderr
        )
        sys.exit(1)
