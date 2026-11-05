"""Wtyczka pobierania zasobów WWW z ochroną przed atakami SSRF.

Weryfikuje schemat (http/https), rozpoznaje adresy IP (DNS) i blokuje
dostęp do wszelkich adresów prywatnych, lokalnych (loopback), link-local i zastrzeżonych.
Weryfikuje również przekierowania (redirects).
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from pathlib import Path
from typing import Callable

import httpx


def _validate_safe_public_host(hostname: str) -> str:
    """Rozwiązuje nazwę hosta i weryfikuje, czy żaden z adresów IP nie jest adresem prywatnym."""
    host = hostname.strip().lower()
    if not host:
        raise ValueError("Pusta nazwa hosta.")

    try:
        addr_info = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        raise ValueError(f"Nie udało się rozwiązać nazwy DNS '{host}': {err}")

    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"Nieprawidłowy adres IP zwrócony przez DNS: {ip_str}")

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise PermissionError(
                f"Odrzucono próbę dostępu do adresu lokalnego/prywatnego (ochrona przed SSRF): {ip} ({host})"
            )
    return host


def register(mcp, run: Callable, clip: Callable, workspace: Path):
    @mcp.tool()
    def web_fetch(url: str, max_bytes: int = 100000) -> str:
        """Pobiera treść strony WWW pod wskazanym adresem URL z rygorystyczną ochroną przed SSRF."""
        raw_url = url.strip()
        if not raw_url:
            return "Błąd: Podano pusty adres URL."

        try:
            parsed = urllib.parse.urlsplit(raw_url)
            if parsed.scheme not in ("http", "https"):
                return f"Błąd bezpieczeństwa: Dozwolone są wyłącznie schematy http i https (podano: {parsed.scheme})."

            if not parsed.hostname:
                return "Błąd: Brak poprawnej nazwy hosta w adresie URL."

            _validate_safe_public_host(parsed.hostname)

            limit = max(1024, min(max_bytes, 1048576))
            current_url = raw_url

            with httpx.Client(timeout=15.0, follow_redirects=False) as client:
                # Obsługa do 5 bezpiecznych przekierowań z walidacją każdego adresu IP
                for _ in range(5):
                    resp = client.get(current_url, headers={"User-Agent": "Debian-Trixie-MCP-WebFetch/1.0"})
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            break
                        next_url = urllib.parse.urljoin(current_url, loc)
                        next_parsed = urllib.parse.urlsplit(next_url)
                        if next_parsed.scheme not in ("http", "https"):
                            return f"Błąd bezpieczeństwa: Przekierowanie na niedozwolony schemat: {next_parsed.scheme}"
                        if next_parsed.hostname:
                            _validate_safe_public_host(next_parsed.hostname)
                        current_url = next_url
                        continue

                    # Mamy ostateczną odpowiedź
                    if resp.status_code >= 400:
                        return f"Błąd HTTP ({resp.status_code}): {resp.reason_phrase}"

                    body = resp.text
                    if len(body.encode("utf-8")) > limit:
                        body = body[:limit] + f"\n\n... [Przycięto: treść przekracza limit {limit} bajtów]"
                    return body

            return "Błąd: Zbyt wiele przekierowań HTTP (przekroczono limit 5)."
        except PermissionError as p_err:
            return f"Odmowa dostępu: {p_err}"
        except Exception as err:
            return f"Błąd pobierania zasobu WWW: {err}"
