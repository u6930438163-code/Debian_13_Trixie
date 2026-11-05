# Debian 13 — niezależna konsola systemowa i projekt OS

**Uruchamialna aplikacja developerska** dla nowego, niezależnego projektu
Debian 13. Zawiera realny Python HTTP API, GUI (czat, Work, diagnostyka,
terminal ograniczony), trwałą kolejkę SQLite, konfigurację czterech
**oryginalnych** agentów właściciela i integracje MCP/AI.

> Uczciwy stan: to NIE jest ukończony, bootowalny system operacyjny,
> obraz ISO, wirtualna maszyna ani uruchomiony autonomiczny agent.
> Skrypty uruchamiają aplikację na hoście z Pythonem (najlepiej Debian 13).
> Testy CI budują i uruchamiają serwer na bazie Debiana 13.

## Start — jedna komenda, bez Dockera i bez Copilota

W nowym Codespace utworzonym z tego repo albo lokalnym terminalu:

```bash
bash debian13/start-local.sh
```

Gdy repozytorium jest już katalogiem roboczym `debian13/`:

```bash
bash start-local.sh
```

Otwórz GUI `http://127.0.0.1:3000`. W Codespaces w panelu **Ports**
otwórz **prywatny** przekierowany port 3000. Nie zmieniaj go na publiczny.
Backend jest prywatny i dostępny wyłącznie na `127.0.0.1:8765`;
token jest automatycznie generowany i przechowywany w prywatnym pliku
`~/.local/state/debian13/operator-token`, nigdy w repozytorium.
`Ctrl+C` zamyka oba procesy. Baza zadań Work zachowuje dane między
uruchomieniami w `~/.local/share/debian13/workspace`.

```bash
bash debian13/start-local.sh --check
python3 -m unittest discover -s debian13/system/tests -v
node --test debian13/ui/dev_server.test.mjs
```

**Wymagania:** Python 3.10+; przeglądarka. Do samego startu aplikacji
nie potrzeba Dockera, npm, płatnego planu GitHub ani klucza API.

## Podłączanie modelu AI — oddzielnie od startu aplikacji

Bez modelu API uczciwie zwraca `503 provider_not_configured` podczas
rozmowy. GUI i Work nadal działają. Aby korzystać z prawdziwego modelu,
ustaw po stronie serwera zmienne:

```bash
export MODEL_CHAT_URL="https://TWOJ-DOSTAWCA/v1/chat/completions"
export MODEL_API_KEY="SEKRET_UMIESZCZONY_W_SEKRETACH"
export MODEL_NAME="nazwa-modelu"
bash debian13/start-local.sh
```

Adres i klucz powyżej to **przykłady**. Nie wklejaj sekretów do GitHub
ani do AI Studio w plikach źródłowych. Wspierane jest też uruchomienie
z Docker Compose + Ollama poprzez `bash debian13/start-ai-session.sh`;
domyślny model Vision 11B wymaga znaczącej pamięci. Szczegóły w
[`debian13/README.md`](debian13/README.md).

## Podłączenie Twoich agentów z SVL_System (czat)

Aria, Alex, Astyria i Aurelius z `mmitek515-netizen/SVL_System` (ich misje, łańcuch
Gemini → xAI → Ollama i pamięć rozmów w jądrze SVL) odpowiadają w czacie Debiana 13:
```bash
export SVL_SYSTEM_URL="https://ADRES-SVL_SYSTEM"   # sam adres, bez ścieżki; lokalnie: http://127.0.0.1:3000
bash debian13/start-local.sh
python3 debian13/system/svl_core.py --reconnect   # Result: connected, via SVL_System
```
SVL_System musi mieć skonfigurowany model (`GEMINI_API_KEY`, `XAI_API_KEY` lub Ollamę).
Kafelki pokazują wtedy „Prawdziwy agent · SVL_System”, a odpowiedzi mają źródło `svl_system`
i silnik `dostawca:model`.

**Bezpieczeństwo (sprawdzone na kodzie i uruchomionym serwerze SVL_System):**
`POST /api/triad/message` w SVL_System **nie ma uwierzytelnienia**, a wiadomość zaczynająca
się od `/exec`, `!`, `/propose|/approve` lub słowa `status/root/sudo/sync/…` jest
**wykonywana na serwerze SVL jako polecenie powłoki/jądra**. Dlatego Debian 13:
- dodaje do każdej wiadomości prefiks `@model ` (SVL go usuwa przed modelem) — polecenia
  docierają do agenta jako zwykły tekst i nigdy nie są wykonywane;
- odrzuca odpowiedzi bez modelu (`without_nlp`, szablony Best-Fit) zamiast je pokazywać;
- przekazuje do SVL **tylko Twój czat** — zadania Work, MCP i GPT Actions nigdy tam nie trafiają.
Sam serwer SVL_System pozostaje otwarty dla każdego, kto zna jego adres: nie publikuj go
bez uwierzytelnienia (np. proxy z hasłem — token podasz w `SVL_SYSTEM_TOKEN`).

## Podłączenie Twoich prawdziwych agentów z AI_Studio_BastillaOS

Aria, Alex, Astyria i Aurelius „żyją” w projekcie `u6930438163-code/AI_Studio_BastillaOS`
(łańcuch modeli Gemini → xAI → Ollama i ich własne misje). Debian 13 łączy się z nimi
protokołem `bastillaos-debian13-v1` (`/api/agent-tasks/capabilities|workers|messages|submit|jobs/<id>`):
czat i zadania Work trafiają do prawdziwego agenta, a Debian 13 pokazuje jego zapisaną odpowiedź.

**1. Po stronie BastillaOS** (sekrety wdrożenia, np. AI Studio → Secrets):
```bash
DEBIAN13_ORIGINAL_WORKERS_ENABLED=1
DEBIAN13_ORIGINAL_AGENTS_TOKEN="<losowy sekret, min. 32 znaki>"
GEMINI_API_KEY=...            # lub XAI_API_KEY / Ollama — co najmniej jeden model
```

**2. Po stronie Debiana 13** (ten sam sekret):
```bash
export ALEX_EXISTING_API_URL="https://TWOJ-ADRES-BASTILLAOS/api"   # musi kończyć się na /api
export BASTILLA_AGENTS_TOKEN="<ten sam sekret>"                    # lub ALEX_EXISTING_API_TOKEN
bash debian13/start-local.sh
```

**3. Ponowne połączenie — Twój SVL Core** (`debian13/system/svl_core.py`):
```bash
python3 debian13/system/svl_core.py --reconnect   # kod wyjścia 0 = wszyscy czterej połączeni
```
To samo robi przycisk **„Połącz ponownie (SVL Core)”** w Ustawieniach i na Pulpicie
(`POST /api/v1/agents/reconnect`). Raport podaje wynik (`connected`, `partial`,
`no_agents_advertised`, `source_offline`, `source_rejected_token`, `not_configured`
z listą brakujących ustawień), a `Agents_Active` w statusie SVL Core zawiera tylko
agentów potwierdzonych przez BastillaOS.

Gdy połączenie działa, kafelki agentów pokazują „Prawdziwy agent · BastillaOS” i
„BastillaOS: połączony”, a odpowiedzi w czacie mają źródło `original_agent`.
Jeśli BastillaOS jest niedostępny, kafelek mówi to wprost (`offline`, `zły token`,
`workery wyłączone`) — żadna odpowiedź nie jest podstawiana lokalnie. Bez połączenia
agenci korzystają z lokalnego modelu opisanego niżej (o ile jest skonfigurowany).

Ograniczenia protokołu po stronie BastillaOS: wiadomość do 2000 znaków, tryb
`model_review_only` (agent odpowiada tekstem, nie wykonuje poleceń), brak pamięci
rozmowy w tym kanale; odpowiedź pojawia się po kilku–kilkudziesięciu sekundach.

## Niezależni agenci: bez ról, bez skryptów

Aria, Astyria, Aurelius i Alex działają niezależnie, zarówno na czacie, jak i w systemie:

- **Brak roli i skryptu.** Do modelu nie trafia żaden prompt systemowy, persona,
  specjalizacja, hierarchia ani gotowa odpowiedź. Model dostaje wyłącznie Twoje
  wiadomości i własne poprzednie odpowiedzi w tej samej rozmowie (sprawdzają to testy).
- **Własny model.** Każdy agent może działać na innym dostawcy lub modelu.
  Nieustawione pole agenta przyjmuje wartość wspólną `MODEL_*`:
  ```bash
  export MODEL_ALEX_NAME="model-alexa"            # tylko inny model, ten sam dostawca
  export MODEL_ARIA_CHAT_URL="https://INNY-DOSTAWCA/v1/chat/completions"
  export MODEL_ARIA_API_KEY="SEKRET_W_SEKRETACH"
  export MODEL_ARIA_NAME="model-arii"
  ```
- **Własna pamięć.** Rozmowa należy do jednego agenta; inny agent nigdy nie widzi
  jej treści (API zwraca `409 session_agent_mismatch`). Pamięć jest w procesie API
  (nie na dysku), do 40 wiadomości na rozmowę, wygasa po 2 h bezczynności.
- **Samodzielna praca.** Worker w tle przekazuje każde zadanie Work przypisanemu
  agentowi — tylko treść zadania, bez instrukcji — i zapisuje jego odpowiedź
  (`completed`) albo kod błędu modelu (`failed`). Agent bez skonfigurowanego modelu
  nie dostaje zadań, więc nic nie jest oznaczane jako wykonane bez prawdziwej
  odpowiedzi. Wyłączenie: `DEBIAN13_AGENT_WORKER=0`.

**Granica bezpieczeństwa:** wynikiem pracy agenta jest tekst do przeglądu. Agent
nie wykonuje poleceń powłoki, nie zmienia plików, nie dostaje roota, nie wywołuje
GitHub i nie robi zakupów.

## Oryginalni agenci AI właściciela

Konfiguracje źródłowych agentów **Alex, Aria, Astyria, Aurelius** w
`.github/agents/*.agent.md` zostały zaadaptowane z repozytorium
`u6930438163-code/AI_Studio_BastillaOS`. NIE są nowymi agentami ChatGPT.
Nie kopiujemy starego systemu ani nie utożsamiamy pliku profilu z
pracującym workerem.

W ustawieniach GUI widać osobny status źródła dla każdego agenta.
`GET /api/v1/original-agents` oddziela dostępność rejestru zadań od
rzeczywistego wykonania przez model. Dopóki prawdziwe API źródła i worker
nie zostały potwierdzone, każdy agent ma status **niepodłączony**.
Ustawienia źródłowego API opisują:
[`debian13/system/ALEX_INTEGRATION.md`](debian13/system/ALEX_INTEGRATION.md)
i [`debian13/system/ALEX_HANDOFF.md`](debian13/system/ALEX_HANDOFF.md).
Potwierdzenie źródłowego rejestru nie potwierdza inferencji.

**Zakupy są tylko propozycjami** — każda rzeczywista transakcja wymaga
oddzielnej zgody właściciela z podaną kwotą i dostawcą. Nie ma integracji
płatniczej ani automatycznych zakupów.

## Google AI Studio — ważne ograniczenie

Można **importować kod** repozytorium w Google AI Studio → Build →
**Add files (+)** → **Import from GitHub**. Projekt zawiera zarówno
statyczne GUI HTML/CSS/JS, jak i osobny backend Python. AI Studio
korzysta z własnego środowiska do budowania aplikacji; **import repo
nie uruchomi automatycznie skryptów Pythona, Dockera, Ollamy ani
oryginalnych agentów**. W AI Studio trzeba przygotować odpowiedni
frontend/proxy lub uruchomić API osobno i bezpiecznie je połączyć.
Nie deklaruj działającej synchronizacji, dopóki nie potwierdzisz jej
w ustawieniach i z GitHubem.

## Architektura i stan

- `debian13/ui/` — responsywny interfejs czatu, Work, terminala i statusu.
- `debian13/system/server.py` — uwierzytelniony HTTP API.
- `debian13/system/work_store.py` — trwała kolejka planów z SQLite
  (**nie wykonuje autonomicznie poleceń**).
- `debian13/system/original_agents.py` — diagnostyka czterech istniejących
  agentów, bez wymyślonych statusów.
- `debian13/system/alex_handoff.py` — bramka przekazania zadań wymagająca
  prawdziwego źródłowego endpointu, obsługiwanej funkcji i autoryzacji.
- `debian13/system/mcp_server.py` — bezpieczne narzędzia MCP tylko do odczytu (stdio).
- `debian13/system/server.py` — uwierzytelniony endpoint MCP `/mcp` (Streamable HTTP) oraz API GPT Actions.
- `server.ts` — chronione trasowanie MCP i GPT Actions do backendu Python.
- `.github/workflows/debian13-validation.yml` — testy na Debian 13,
  test rzeczywistej aplikacji i budowy kontenera.

Konfigurację połączeń MCP, Codex, oryginalnego Alexa i GPT Actions oraz wymagane testy opisuje [instrukcja integracji](debian13/system/INTEGRATIONS.md). Sam import repozytorium nie oznacza aktywnego MCP ani podłączonego modelu AI.

**Pełne uruchomienie samodzielnego systemu operacyjnego** (boot/ISO,
sterowniki, instalacja i cały cykl agenta) nadal wymaga odrębnych etapów
i weryfikacji sprzętowej; sukces CI aplikacji nie jest takim dowodem.

## Nowy design — źródło wyświetlane w AI Studio i Codespaces

**Jedyny kanoniczny interfejs:** `debian13/ui/index.html`, `debian13/ui/styles.css`
oraz `debian13/ui/app.mjs`. Serwer Node (`server.ts`) i lokalny serwer Python
wyświetlają właśnie ten interfejs. Główny `index.html` jest tylko przekierowaniem
dla podglądu Vite/AI Studio do `debian13/ui/`. Pliki `src/App.tsx` nie są wejściem
uruchomionej konsoli, więc ich edycja nie zmienia wyświetlanego GUI.

Nowy wygląd obejmuje kafelki agentów, duży czat, diagnostykę, lokalne
reakcje 👍/👎, terminal i ograniczone interaktywne skrypty. Statusy agentów
wymagają potwierdzenia API, a brak połączenia nie jest symulowany.

Po wypchnięciu kodu do GitHub otwórz **AI Studio → Settings → GitHub → Pull
changes** dla podłączonego repozytorium. Sam push na GitHub nie potwierdza
aktualizacji uruchomionej aplikacji ani automatycznej synchronizacji.
HTML, CSS i JavaScript nie są cache'owane w serwerach podglądu, co zmniejsza
ryzyko wyświetlania starych zasobów.
