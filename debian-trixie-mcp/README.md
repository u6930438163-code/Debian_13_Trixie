# Kompletny Serwer Model Context Protocol (MCP) dla Debiana 13 "Trixie"

Zaawansowany, produkcyjny serwer **Model Context Protocol (MCP)** przygotowany specjalnie dla systemu **Debian 13 (Trixie)**. Zapewnia bezpieczną i pełną integrację asystentów AI (Claude Code, Claude Desktop, OpenAI, Ollama) z lokalną maszyną z systemem Debian.

---

## 🚀 Główne Cechy i Architektura

- **Zgodność z PEP 668 (Debian 13 Trixie):** Środowisko instalowane jest w dedykowanym `.venv`. Nigdy nie narusza systemowego menedżera pakietów ani globalnego Pythona.
- **Dwa tryby transportu:**
  - **`stdio` (Domyślny):** Przeznaczony dla Claude Code i Claude Desktop. Standardowe wyjście (`stdout`) jest w 100% zarezerwowane na protokół MCP JSON-RPC; wszystkie komunikaty diagnostyczne i logi trafiają na `stderr`.
  - **`streamable-http` (SSE / Starlette na `127.0.0.1:8765`):** Zabezpieczony wymaganym tokenem `Bearer`. Token jest porównywany za pomocą `hmac.compare_digest` (ochrona przed Timing Attack). Przy braku lub pustym tokenie serwer kategorycznie odmawia startu.
- **Modułowa architektura wtyczek (`plugins/`):** Aktywacja wtyczek za pomocą zmiennej środowiskowej `MCP_PLUGINS=system,command,filesystem,...` (lub `*` dla wszystkich). Każda wtyczka definiuje funkcję `register(mcp, run, clip, workspace)`.
- **Dwuwarstwowe bezpieczeństwo powłoki i Roota (`root_shell`):**
  - `shell_exec`: polecenia zwykłego użytkownika z timeoutem i audytem (`SHELL_ENABLED=1`).
  - `root_exec`: polecenia administracyjne roota przez `sudo`.
  - **Tryb `ROOT_SHELL_MODE=approve` (Domyślny i bezpieczny):** AI **nie może** samodzielnie wykonać komendy jako root. Zadanie trafia do kolejki w `~/.local/state/debian-trixie-mcp/pending/`, a operator zatwierdza je w terminalu skryptem `bin/root-approve` (podając swoje hasło sudo). AI sprawdza wynik narzędziem `root_result(id)`.
  - **Tryb `ROOT_SHELL_MODE=open`:** Natychmiastowe wykonanie przez `sudo -n`, wymaga reguły sudoers zainstalowanej przez `install.sh --root-open`.
  - **Rejestr audytu:** Pełny zapis każdego wywołania powłoki i roota w pliku JSON Lines (`audit.jsonl`) poza katalogiem roboczym.

---

## 📦 Zestaw Narzędzi MCP

| Kategoria | Narzędzia | Opis działania |
| :--- | :--- | :--- |
| **System** | `system_info`, `apt_search`, `apt_show`, `apt_upgradable`, `systemd_status`, `journal_tail` | Pełna inspekcja parametrów Debiana 13, pakiety APT, logi systemd. |
| **Polecenia** | `run_command` | Dozwolone komendy diagnostyczne z białej listy (`ls`, `ps`, `df`, `ip` itp.), bez powłoki (`shell=False`), parsowane przez `shlex.split`, blokada flag destrukcyjnych (np. `find -delete`, `git -c`). |
| **Pliki i Git** | `list_dir`, `read_file`, `write_file`, `git_status`, `git_diff`, `git_log`, `git_commit` | Izolowane operacje wyłącznie wewnątrz katalogu roboczego (ochrona przed Path Traversal), blokada zapisu do `.git` i `.env`. |
| **Most AI** | `post_message`, `read_messages`, `ask_ai` | Atomowa skrzynka wiadomości JSONL z blokowaniem `fcntl.flock`, odpytywanie modeli Gemini (Google AI Studio) oraz OpenAI / Ollama. |
| **Gemini** | `gemini_models`, `gemini_chat`, `gemini_ask_about_files`, `gemini_agent` | Czat z trwałą historią sesji, analiza plików projektu, autonomiczna pętla Function Calling z narzędziami tylko do odczytu. |
| **GitHub** | `github_user`, `github_repo`, `github_issues`, `github_pulls`, `github_create_issue` | Integracja REST API GitHub. Operacje zapisu wymagają `GITHUB_ALLOW_WRITE=1`. |
| **Kontenery** | `container_list`, `container_images`, `container_logs`, `container_action` | Podgląd Docker / Podman. Start/stop/restart wymaga flagi `CONTAINER_ALLOW_CONTROL=1`. |
| **Web Fetch** | `web_fetch` | Pobieranie stron WWW z rygorystyczną ochroną przed SSRF (blokada IP prywatnych, localhost, link-local dla każdego przekierowania). |
| **Tmux** | `tmux_list_sessions`, `tmux_list_windows`, `tmux_capture_pane`, `tmux_send_keys` | Inspekcja sesji tmux. Wysyłanie klawiszy wymaga `TMUX_ALLOW_SEND=1`. |
| **Systemd** | `systemd_service_action` | Sterowanie wybranymi usługami z białej listy `SYSTEMD_ALLOWED_SERVICES`. |
| **Notify** | `notify_ntfy` | Wysyłanie powiadomień push na telefon lub pulpit przez serwer ntfy. |
| **Root Shell** | `shell_exec`, `root_exec`, `root_pending`, `root_result` | Bezpieczna powłoka użytkownika i kolejka zatwierdzania komend root. |

---

## 🛠️ Szybka Instalacja na Twojej Maszynie

### 1. Pobranie i uruchomienie instalatora
```bash
chmod +x install.sh
./install.sh
```

Instalator automatycznie:
1. Sprawdzi pakiety systemowe APT (`python3`, `python3-venv`, `git`, `curl`, `jq`, `tmux`, `sudo`).
2. Utworzy wirtualne środowisko `.venv` i zainstaluje zależności (`mcp`, `httpx`, `python-dotenv`, `uvicorn`, `starlette`).
3. Wygeneruje bezpieczny 64-znakowy Bearer Token i utworzy plik `.env` z prawami dostępu `chmod 600`.
4. Przygotuje katalogi stanu w `~/.local/state/debian-trixie-mcp`.
5. Przeprowadzi pełną diagnostykę narzędziem `bin/doctor`.

---

## ⚙️ Integracja z Asystentami AI

### A. Tryb STDIO (Claude Code)
Uruchom w swoim terminalu:
```bash
claude mcp add debian-trixie -- /sciezka/do/debian-trixie-mcp/.venv/bin/python3 /sciezka/do/debian-trixie-mcp/server.py --transport stdio
```

### B. Konfiguracja Claude Desktop (`claude_desktop_config.json`)
W pliku `~/.config/Claude/claude_desktop_config.json` dodaj:
```json
{
  "mcpServers": {
    "debian-trixie": {
      "command": "/pelna/sciezka/do/debian-trixie-mcp/.venv/bin/python3",
      "args": [
        "/pelna/sciezka/do/debian-trixie-mcp/server.py",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

### C. Tryb HTTP / SSE (Sieciowy)
1. Uruchom serwer w trybie HTTP:
   ```bash
   ./.venv/bin/python3 server.py --transport http
   ```
2. Parametry połączenia:
   - **Adres URL:** `http://127.0.0.1:8765/sse`
   - **Wymagany nagłówek HTTP:** `Authorization: Bearer <TWÓJ_TOKEN_Z_PLIKU_.ENV>`

---

## 🛡️ Zatwierdzanie Poleceń Root (`bin/root-approve`)

Gdy AI wywoła narzędzie `root_exec`, w domyślnym trybie polecenie **nie jest wykonywane natychmiast**. Serwer zwraca identyfikator zadania (np. `a1b2c3d4e5`), a polecenie trafia do kolejki.

1. W terminalu uruchom:
   ```bash
   ./bin/root-approve
   ```
2. Skrypt wyświetli ID, czas zgłoszenia oraz dokładną treść komendy roota:
   ```text
   --------------------------------------------------------------------
   ID ZADANIA:    a1b2c3d4e5
   POLECENIE DO WYKONANIA:
     apt-get update && apt-get install -y htop
   --------------------------------------------------------------------
   Zatwierdzić polecenie root? [y/N/r/q] (y=wykonaj, N=odrzuć, r=szczegóły, q=wyjdź):
   ```
3. Wybierz:
   - **`y`**: polecenie zostanie wykonane przez `sudo bash -c` (system zapyta Cię o Twoje hasło użytkownika). Wynik zostanie zapisany w `results/a1b2c3d4e5.json`.
   - **`N`**: polecenie zostanie odrzucone.
4. AI automatycznie sprawdza wynik za pomocą `root_result("a1b2c3d4e5")`.

---

## 🩺 Diagnostyka Systemu (`bin/doctor`)

Aby w dowolnym momencie sprawdzić stan serwera, uprawnienia `.env`, środowisko `.venv` i konfigurację sudo, uruchom:
```bash
./bin/doctor
```

---

## 📤 Publikacja Repozytorium (`publish.sh`)

Aby bezpiecznie zainicjalizować repozytorium Git i wysłać kod na GitHub bez ryzyka wycieku pliku `.env`:
```bash
./publish.sh "Inicjalizacja serwera MCP dla Debiana 13"
```
Skrypt gwarantuje, że pliki zawierające sekrety nigdy nie trafią do zdalnego repozytorium.
