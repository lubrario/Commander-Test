#!/bin/zsh
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 não encontrado. Instale Python 3.10+ para iniciar o Taberna Vault."
  read -k 1 "?Pressione uma tecla para sair."
  exit 1
fi
(sleep 2; open 'http://127.0.0.1:8765') &
echo 'Taberna Vault: http://127.0.0.1:8765'
python3 app.py
