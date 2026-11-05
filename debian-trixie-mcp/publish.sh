#!/usr/bin/env bash
# ==============================================================================
# Skrypt publikacji repozytorium Git - Debian 13 Trixie MCP Server
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
echo -e "${COLOR_BLUE}           PUBLIKACJA REPOZYTORIUM GIT (PUBLISH.SH)                ${COLOR_RESET}"
echo -e "${COLOR_BLUE}====================================================================${COLOR_RESET}"

# 1. Inicjalizacja repozytorium Git, jeśli nie istnieje
if [[ ! -d ".git" ]]; then
  echo "Inicjalizacja lokalnego repozytorium Git (gałąź main)..."
  git init -b main
else
  echo "Repozytorium Git już istnieje."
fi

# 2. Rygorystyczna kontrola ochrony sekretów (.gitignore)
if [[ ! -f ".gitignore" ]] || ! grep -q "^\.env" ".gitignore"; then
  echo -e "${COLOR_RED}BŁĄD KRYTYCZNY: Brak wpisu .env w pliku .gitignore! Zatrzymano publikację.${COLOR_RESET}"
  exit 1
fi

# 3. Sprawdzenie, czy plik .env nie został omyłkowo dodany do indeksu
git add .
if git status --porcelain | grep -E "^\s*[AM].*\.env(\s|$)"; then
  echo -e "${COLOR_RED}BŁĄD KRYTYCZNY: Wykryto plik .env w indeksie Git! Usuwanie z indeksu...${COLOR_RESET}"
  git reset HEAD .env
  echo "Plik .env został wycofany z indeksu i nie zostanie wysłany."
  exit 1
fi

# 4. Tworzenie commita
COMMIT_MSG="${1:-Inicjalizacja serwera MCP dla Debiana 13 Trixie}"
if git diff --staged --quiet; then
  echo -e "${COLOR_YELLOW}Brak nowych zmian do zatwierdzenia (drzewo robocze czyste).${COLOR_RESET}"
else
  git commit -m "${COMMIT_MSG}"
  echo -e "${COLOR_GREEN}Pomyślnie utworzono commit: '${COMMIT_MSG}'${COLOR_RESET}"
fi

# 5. Konfiguracja zdalnego repozytorium i push
CURRENT_REMOTE="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "${CURRENT_REMOTE}" ]]; then
  echo ""
  echo "Repozytorium nie ma jeszcze skonfigurowanego zdalnego adresu 'origin'."
  read -r -p "Podaj adres zdalnego repozytorium (np. git@github.com:twoj-user/debian-mcp.git) [Enter aby pominąć]: " REMOTE_URL
  if [[ -n "${REMOTE_URL}" ]]; then
    git remote add origin "${REMOTE_URL}"
    echo -e "Dodano origin: ${COLOR_GREEN}${REMOTE_URL}${COLOR_RESET}"
    echo "Wysyłanie zmian na zdalny serwer (git push -u origin main)..."
    git push -u origin main
    echo -e "${COLOR_GREEN}Repozytorium zostało pomyślnie opublikowane!${COLOR_RESET}"
  else
    echo "Pominięto push. Możesz dodać zdalne repozytorium później:"
    echo "  git remote add origin <adres_repozytorium>"
    echo "  git push -u origin main"
  fi
else
  echo "Zdalne repozytorium 'origin': ${CURRENT_REMOTE}"
  read -r -p "Czy chcesz teraz wysłać zmiany na zdalny serwer (git push)? [T/n]: " DO_PUSH
  if [[ "${DO_PUSH:-t}" =~ ^[tTyY] ]]; then
    git push origin main
    echo -e "${COLOR_GREEN}Zmiany zostały pomyślnie wysłane do zdalnego repozytorium!${COLOR_RESET}"
  fi
fi
