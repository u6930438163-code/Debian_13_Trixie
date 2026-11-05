#!/usr/bin/env bash
# ==============================================================================
# Skrypt instalacyjny Debian 13 Trixie MCP Server
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

COLOR_GREEN="\033[1;32m"
COLOR_YELLOW="\033[1;33m"
COLOR_RED="\033[1;31m"
COLOR_BLUE="\033[1;34m"
COLOR_RESET="\033[0m"

echo -e "${COLOR_BLUE}====================================================================${COLOR_RESET}"
echo -e "${COLOR_BLUE}    INSTALACJA SERWERA MODEL CONTEXT PROTOCOL (MCP) - DEBIAN 13     ${COLOR_RESET}"
echo -e "${COLOR_BLUE}====================================================================${COLOR_RESET}"

# 1. Sprawdzenie, czy nie uruchomiono jako root
if [[ "${EUID}" -eq 0 ]]; then
  echo -e "${COLOR_RED}BŁĄD: Nie uruchamiaj install.sh bezpośrednio jako root!${COLOR_RESET}"
  echo "Serwer MCP jest zaprojektowany do działania jako zwykły użytkownik."
  echo "Skrypt sam poprosi o uprawnienia sudo tylko wtedy, gdy będzie to konieczne (np. apt)."
  exit 1
fi

CURRENT_USER="$(id -un)"
echo -e "Instalacja dla użytkownika: ${COLOR_GREEN}${CURRENT_USER}${COLOR_RESET}"

# 2. Instalacja pakietów systemowych APT
echo -e "\n${COLOR_YELLOW}[1/6] Sprawdzanie i instalacja wymaganych pakietów systemowych APT...${COLOR_RESET}"
REQUIRED_PKGS=(python3 python3-venv python3-pip git curl jq tmux sudo)
MISSING_PKGS=()

for pkg in "${REQUIRED_PKGS[@]}"; do
  if ! dpkg -s "${pkg}" &>/dev/null; then
    MISSING_PKGS+=("${pkg}")
  fi
done

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
  echo "Brakujące pakiety: ${MISSING_PKGS[*]}"
  echo "Wymagane uprawnienia sudo do instalacji pakietów systemowych..."
  sudo apt-get update -qq
  sudo apt-get install -y "${MISSING_PKGS[@]}"
else
  echo -e "${COLOR_GREEN}Wszystkie pakiety APT są już zainstalowane.${COLOR_RESET}"
fi

# 3. Przygotowanie wirtualnego środowiska .venv (zgodność z PEP 668 na Debianie 13)
echo -e "\n${COLOR_YELLOW}[2/6] Tworzenie środowiska wirtualnego .venv (PEP 668)...${COLOR_RESET}"
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
  echo -e "${COLOR_GREEN}Utworzono .venv.${COLOR_RESET}"
else
  echo "Środowisko .venv już istnieje."
fi

echo "Instalacja zależności Python wewnątrz .venv..."
"${SCRIPT_DIR}/.venv/bin/pip" install --upgrade pip -q
"${SCRIPT_DIR}/.venv/bin/pip" install -r requirements.txt -q
echo -e "${COLOR_GREEN}Zależności zainstalowane pomyślnie.${COLOR_RESET}"

# 4. Konfiguracja pliku .env i generowanie tokenu Bearer
echo -e "\n${COLOR_YELLOW}[3/6] Konfiguracja pliku .env i generowanie Bearer Tokenu...${COLOR_RESET}"
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  # Generowanie 64-znakowego bezpiecznego tokenu szesnastkowego
  RANDOM_TOKEN="$("${SCRIPT_DIR}/.venv/bin/python3" -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i "s|MCP_BEARER_TOKEN=.*|MCP_BEARER_TOKEN=${RANDOM_TOKEN}|g" .env
  chmod 600 .env
  echo -e "${COLOR_GREEN}Utworzono plik .env z nowym, unikalnym tokenem Bearer.${COLOR_RESET}"
  echo -e "Uprawnienia pliku .env ustawiono na: ${COLOR_GREEN}chmod 600${COLOR_RESET}"
else
  chmod 600 .env
  echo "Plik .env już istnieje. Uprawnienia zweryfikowane (chmod 600)."
fi

# 5. Tworzenie katalogów stanu i kolejki zadań
echo -e "\n${COLOR_YELLOW}[4/6] Przygotowanie katalogów stanu w ~/.local/state/debian-trixie-mcp...${COLOR_RESET}"
STATE_DIR="${HOME}/.local/state/debian-trixie-mcp"
mkdir -p "${STATE_DIR}/pending" "${STATE_DIR}/results" "${STATE_DIR}/gemini_sessions"
chmod 700 "${STATE_DIR}"
touch "${STATE_DIR}/audit.jsonl"
chmod 600 "${STATE_DIR}/audit.jsonl"
echo -e "${COLOR_GREEN}Katalogi stanu przygotowane.${COLOR_RESET}"

# Nadanie uprawnień wykonywalności skryptom pomocniczym
chmod +x bin/doctor bin/root-approve server.py publish.sh

# 6. Opcjonalna konfiguracja trybu --root-open
if [[ "${1:-}" == "--root-open" ]]; then
  echo -e "\n${COLOR_RED}====================================================================${COLOR_RESET}"
  echo -e "${COLOR_RED}             KONFIGURACJA TRYBU ROOT_SHELL_MODE=open                ${COLOR_RESET}"
  echo -e "${COLOR_RED}====================================================================${COLOR_RESET}"
  echo "OSTRZEŻENIE: Ten tryb instaluje regułę w /etc/sudoers.d/ zezwalającą"
  echo "użytkownikowi '${CURRENT_USER}' na bezhasłowe uruchamianie bash i env."
  echo "Modele AI będą mogły natychmiast wykonywać polecenia roota bez pytania o hasło!"
  echo ""
  read -r -p "Czy na pewno chcesz zainstalować tę regułę? Wpisz 'tak', aby potwierdzić: " CONFIRM
  if [[ "${CONFIRM}" == "tak" ]]; then
    TEMP_SUDOERS="$(mktemp)"
    cat <<EOF > "${TEMP_SUDOERS}"
# Debian 13 Trixie MCP Server - Regula bezhaslowego sudo dla roota
${CURRENT_USER} ALL=(ALL) NOPASSWD: /usr/bin/bash, /usr/bin/env
EOF
    echo "Weryfikacja składni reguły za pomocą visudo -cf..."
    if sudo visudo -cf "${TEMP_SUDOERS}"; then
      sudo install -m 0440 "${TEMP_SUDOERS}" "/etc/sudoers.d/debian-trixie-mcp"
      rm -f "${TEMP_SUDOERS}"
      sed -i 's/^ROOT_SHELL_MODE=.*/ROOT_SHELL_MODE=open/' .env
      echo -e "${COLOR_GREEN}Reguła sudoers zainstalowana pomyślnie. Tryb ROOT_SHELL_MODE=open włączony w .env.${COLOR_RESET}"
      echo ""
      echo -e "${COLOR_YELLOW}JAK COFNĄĆ TĘ REGUŁĘ W DOWOLNYM MOMENCIE:${COLOR_RESET}"
      echo "  sudo rm -f /etc/sudoers.d/debian-trixie-mcp"
      echo "  sed -i 's/^ROOT_SHELL_MODE=.*/ROOT_SHELL_MODE=approve/' .env"
    else
      rm -f "${TEMP_SUDOERS}"
      echo -e "${COLOR_RED}Błąd walidacji sudoers! Anulowano instalację reguły.${COLOR_RESET}"
    fi
  else
    echo "Anulowano konfigurację --root-open. Pozostawiono bezpieczny tryb 'approve'."
  fi
fi

# 7. Opcjonalna instalacja usługi systemd --user
echo -e "\n${COLOR_YELLOW}[5/6] Opcjonalna konfiguracja usługi systemd w trybie --user...${COLOR_RESET}"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SYSTEMD_USER_DIR}/debian-trixie-mcp.service"

mkdir -p "${SYSTEMD_USER_DIR}"
cat <<EOF > "${SERVICE_FILE}"
[Unit]
Description=Debian 13 Trixie Model Context Protocol (MCP) Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/server.py --transport http
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
EnvironmentFile=${SCRIPT_DIR}/.env

[Install]
WantedBy=default.target
EOF

echo -e "Wygenerowano plik usługi: ${COLOR_GREEN}${SERVICE_FILE}${COLOR_RESET}"
echo "Aby uruchomić serwer w tle jako usługę systemd usera, wykonaj:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now debian-trixie-mcp"

# 8. Uruchomienie skryptu kontrolnego doctor
echo -e "\n${COLOR_YELLOW}[6/6] Uruchamianie skryptu kontrolnego bin/doctor...${COLOR_RESET}"
"${SCRIPT_DIR}/bin/doctor" || true

echo -e "\n${COLOR_GREEN}====================================================================${COLOR_RESET}"
echo -e "${COLOR_GREEN}               INSTALACJA ZAKOŃCZONA POMYŚLNIE!                    ${COLOR_RESET}"
echo -e "${COLOR_GREEN}====================================================================${COLOR_RESET}"
echo "Aby przetestować serwer w trybie STDIO (np. z Claude Desktop / Code):"
echo "  ${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/server.py --transport stdio"
echo ""
echo "Aby uruchomić serwer HTTP (SSE) na porcie 8765:"
echo "  ${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/server.py --transport http"
echo ""
