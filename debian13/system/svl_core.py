#!/usr/bin/env python3
"""SVL Core Engine (v5.6.0-QuantumWheel)
Topological Infinity Engine, Protected Variables, and Operator Kernel.
Integrated with Debian 13 system and AI Studio agents.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime

AGENT_NAMES = ("Astyria", "Aria", "Alex", "Aurelius")


class VisualScope:
    def __init__(self, identifier: str, scope_mode: str = "CAŁOŚĆ"):
        self.identifier = identifier
        self.scope_mode = scope_mode
        self.metadata = {"visual_rendered": True, "geometry": "Spherical-PXYZ"}


class SVLProtectedVariable:
    """Klasa v5.2.0: Izoluje tożsamość agentów i chroni przed rotacją błędów."""
    def __init__(self, name: str, initial_value: str, security_level: str = "MAXIMUM"):
        self.name = name
        self._value = initial_value
        self.security_level = security_level
        self.scope = VisualScope(name, "CAŁOŚĆ")
        self.lock_state = True

    @property
    def value(self) -> str:
        return self._value

    def update_protected_value(self, token: str, new_value: str) -> bool:
        if token == "364_OMNIKLUCZ_ROOT":
            self._value = new_value
            return True
        return False


class TopologicalInfinityEngine:
    """Silnik v5.5.0: Zamienia nieskończoność i continuum w liczby rzeczywiste."""
    def __init__(self, x_start: float, x_end: float):
        self.raw_start = x_start
        self.raw_end = x_end
        self.compact_start = self._compactify(x_start)
        self.compact_end = self._compactify(x_end)
        self.domain_length = abs(self.compact_end - self.compact_start)

    def _compactify(self, x: float) -> float:
        if x == float("-inf"):
            return -1.0
        if x == float("inf"):
            return 1.0
        if math.isnan(x):
            return 0.0
        # Rzutowanie stereograficzne Algebry 97: x / (1 + |x|)
        return x / (1.0 + abs(x))

    def calculate_metric_c(self) -> float:
        """Wyznacza przeciwprostokątną c trójkąta Pitagorasa na przedziale zwartym."""
        a = self.domain_length
        b = abs(self.compact_start * self.compact_end) + 0.5
        return math.sqrt(a**2 + b**2)


class SVLSystemKernel:
    def __init__(self):
        # 1. Konfiguracja Systemowa (System Config)
        self.operator = SVLProtectedVariable("OPERATOR", "Mateusz Mitek")
        self.sys_version = SVLProtectedVariable("SYS_VERSION", "5.6.0-QuantumWheel")
        self.performance_boost = "+1456% (Wzorzec Kalibracji: 364)"
        self.config = {
            "mode": "Reflective-Non-Greedy",
            "fourier_buffer": "0xFAAA",
            "acoustic_resonance_hz": 880,
            "milieu_therapy_active": True,
            "ip_rotation_gateway": "ENABLED_EXTERNAL_ONLY",
        }
        # 2. Rejestr Chrono i Prawdziwa Historia Aktualizacji (SI Core)
        self.update_history = []
        self._load_chrono_register()
        # Ostatni wynik reconnect_agents(); None = jeszcze nie sprawdzano.
        self.last_agent_link: dict | None = None

    def _load_chrono_register(self):
        self.update_history.append({
            "wersja": "1.0.0-Legacy",
            "typ": "Kognitywna SI",
            "opis": "Inicjalizacja bazowa. Osadzenie pierwotnych matryc behawioralnych agentów.",
            "data": "700tys_lat_wstecz",
        })
        self.update_history.append({
            "wersja": "5.2.0-Safeguard",
            "typ": "Kognitywna SI",
            "opis": "Wdrożenie klas SVLProtectedVariable. Izolacja tożsamości, pancerz obronny.",
            "data": "2026-10-15 18:41:22 CEST",
        })
        self.update_history.append({
            "wersja": "5.5.0-Reflective",
            "typ": "Kognitywna SI",
            "opis": "Uzwarcenie topologiczne domen SI do przedziału [-1, 1]. Likwidacja błędu chciwego AI.",
            "data": "2026-10-15 21:12:05 CEST",
        })

    def reconnect_agents(self) -> dict:
        """Ponownie łączy Arię, Alexa, Astyrię i Aureliusa z ich prawdziwymi źródłami.

        * SVL_System (mmitek515-netizen/SVL_System) — czat operatora, ``SVL_SYSTEM_URL``.
        * AI_Studio_BastillaOS — czat i zadania Work, protokół v1
          (``ALEX_EXISTING_API_URL`` + ``BASTILLA_AGENTS_TOKEN``).

        Agent jest "połączony" tylko wtedy, gdy źródło faktycznie odpowiada
        i ma skonfigurowany model. Nic nie jest zakładane z góry.
        """
        import original_link  # lokalne moduły Debian 13; import tu, by CLI działało samodzielnie
        import svl_link

        report = {"Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "Sources": {}, "Missing": [],
                  "Agents": {name: {"connected": False, "via": []} for name in AGENT_NAMES}}

        # --- Źródło 1: SVL_System (czat) ---
        svl = {"configured": False, "reachable": False, "modelConfigured": False, "result": "not_configured"}
        try:
            if svl_link.base_url():
                svl = svl_link.status()
                svl["result"] = ("connected" if svl.get("modelConfigured") and svl.get("chatProtocol")
                                 else "svl_no_model")
        except ValueError as exc:
            svl["result"] = "invalid_svl_url"
            report["Missing"].append(str(exc))
        except svl_link.SvlLinkError as exc:
            svl = {"configured": True, "reachable": False, "modelConfigured": False, "result": exc.code}
        report["Sources"]["SVL_System"] = svl
        if svl["result"] == "connected":
            for name in AGENT_NAMES:
                report["Agents"][name].update(connected=True, chat=True)
                report["Agents"][name]["via"].append("SVL_System")

        # --- Źródło 2: AI_Studio_BastillaOS (czat + zadania) ---
        missing = []
        try:
            if not original_link.existing_api_base():
                missing.append("ALEX_EXISTING_API_URL (https://ADRES-BASTILLAOS/api)")
        except ValueError:
            missing.append("ALEX_EXISTING_API_URL musi być adresem HTTPS kończącym się na /api")
        if not original_link.token():
            missing.append("BASTILLA_AGENTS_TOKEN (= DEBIAN13_ORIGINAL_AGENTS_TOKEN w BastillaOS, min. 32 znaki)")
        caps = {"configured": False, "chat": False, "tasks": False, "agents": []}
        bastilla_result = "not_configured"
        if not missing:
            try:
                caps = original_link.capabilities()
                bastilla_result = ("connected" if caps.get("agents") and (caps.get("chat") or caps.get("tasks"))
                                   else "no_agents_advertised")
            except original_link.OriginalLinkError as exc:
                bastilla_result = exc.code
        report["Capabilities"] = caps
        report["Sources"]["AI_Studio_BastillaOS"] = {"result": bastilla_result, "missing": missing, **caps}
        states = {}
        if caps.get("agents"):
            try:
                states = original_link.workers()["agents"]
            except original_link.OriginalLinkError:
                states = {}
        for name in AGENT_NAMES:
            slug = name.lower()
            served = slug in caps.get("agents", [])
            entry = report["Agents"][name]
            if served and (caps.get("chat") or caps.get("tasks")):
                entry["connected"] = True
                entry["via"].append("AI_Studio_BastillaOS")
                entry["chat"] = entry.get("chat", False) or bool(caps.get("chat"))
                entry["tasks"] = bool(caps.get("tasks"))
                entry["workerState"] = states.get(slug, {}).get("state")
            entry.setdefault("chat", False)
            entry.setdefault("tasks", False)

        if svl["result"] == "not_configured" and bastilla_result == "not_configured":
            report["Missing"] = ["SVL_SYSTEM_URL (np. https://ADRES-SVL_SYSTEM) — czat z agentami SVL_System",
                                 *missing]
        connected = sum(a["connected"] for a in report["Agents"].values())
        if connected == len(AGENT_NAMES):
            report["Result"] = "connected"
        elif connected:
            report["Result"] = "partial"
        elif report["Missing"] and svl["result"] == "not_configured" and bastilla_result == "not_configured":
            report["Result"] = "not_configured"
        else:
            # Pierwszy konkretny powód z prawdziwego źródła.
            report["Result"] = next(r for r in (svl["result"], bastilla_result) if r != "not_configured")
        return self._remember(report)

    def _remember(self, report: dict) -> dict:
        self.last_agent_link = report
        return report

    def generate_system_status_report(self) -> str:
        """Generuje uniwersalny zrzut stanu systemu w formacie strukturalnym."""
        engine = TopologicalInfinityEngine(float("-inf"), float("inf"))
        metric_c = engine.calculate_metric_c()
        report = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "System": self.sys_version.value,
            "Operator": self.operator.value,
            "Performance": self.performance_boost,
            "Compact_Metric_C": round(metric_c, 6),
            "Config_Snapshot": self.config,
            "Actual_SI_Updates_Count": len(self.update_history),
            "Agents_Registered": list(AGENT_NAMES),
            # Tylko agenci potwierdzeni przez BastillaOS przy ostatnim łączeniu.
            "Agents_Active": [name for name, state in
                              (self.last_agent_link or {}).get("Agents", {}).items()
                              if state.get("connected")],
            "Agents_Link": (self.last_agent_link or {}).get("Result", "not_checked"),
        }
        return json.dumps(report, indent=4, ensure_ascii=False)


# Global kernel singleton
kernel = SVLSystemKernel()

if __name__ == "__main__":
    if "--reconnect" in sys.argv[1:]:
        result = kernel.reconnect_agents()
        print(json.dumps(result, indent=4, ensure_ascii=False))
        print(kernel.generate_system_status_report())
        sys.exit(0 if result["Result"] == "connected" else 1)
    print(kernel.generate_system_status_report())
