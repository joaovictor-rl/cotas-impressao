#!/usr/bin/env bash
# Use quando as impressoras pararem de imprimir.
# Uso:  bash REPARAR_LINUX.sh
set -u
[ "$(id -u)" -eq 0 ] || exec sudo bash "$(readlink -f "$0")" "$@"
ORIGEM="$(cd "$(dirname "$0")" && pwd)"

# Programa apagado ou quebrado: as impressoras voltam ao endereço original
if { [ ! -e /usr/lib/cups/backend/cotas ] && [ ! -e /usr/libexec/cups/backend/cotas ]; } \
   || [ ! -d /opt/cotas-impressao ]; then
  echo ">> O controle de cotas não está instalado direito; devolvendo os endereços originais..."
  ( cd "$ORIGEM" && python3 -m cotas.restaurar )
fi

echo ">> Reativando as impressoras..."
for impressora in $(LC_ALL=C lpstat -e 2>/dev/null); do
  cupsenable "$impressora" && cupsaccept "$impressora" && echo "   $impressora reativada"
done

if [ -n "$(lpstat -W not-completed -o 2>/dev/null)" ]; then
  lpstat -W not-completed -o
  read -r -p "Cancelar esses documentos presos na fila? [s/N] " resposta || resposta=""
  [[ "$resposta" =~ ^[sS]$ ]] && cancel -a && echo "   Fila limpa."
fi

echo ">> Reiniciando o sistema de impressão..."
systemctl restart cups
sleep 2

if [ -d /opt/cotas-impressao ] && command -v cotas-admin >/dev/null; then
  echo
  cotas-admin diagnostico
fi
