#!/usr/bin/env bash
# Remove o controle de cotas e devolve as impressoras ao endereço original.
# Uso:  bash desinstalar_linux.sh        (mantém o histórico)
#       bash desinstalar_linux.sh --tudo (apaga também config e histórico)
set -u
[ "$(id -u)" -eq 0 ] || exec sudo bash "$(readlink -f "$0")" "$@"

systemctl disable --now cotas-impressao.service 2>/dev/null
rm -f /etc/systemd/system/cotas-impressao.service
systemctl daemon-reload

# IMPORTANTE: devolve cada impressora ao endereço original
# (tem que vir antes de apagar o backend, senão as impressoras param)
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
( cd "$ORIGEM" && python3 -m cotas.restaurar )

# Só apaga o backend se nenhuma impressora ainda depender dele.
if LC_ALL=C lpstat -v 2>/dev/null | grep -q ": cotas:/"; then
  echo "ERRO: ainda há impressoras usando cotas:/ (veja 'lpstat -v'). O backend foi mantido."
  exit 1
fi
rm -f /usr/lib/cups/backend/cotas /usr/libexec/cups/backend/cotas
# ícones que o painel criou na Área de Trabalho de cada usuário
for casa in /home/*; do
  find "$casa" -maxdepth 2 -name cotas-painel.desktop -delete 2>/dev/null
done
rm -f /etc/xdg/autostart/cotas-painel.desktop /usr/share/applications/cotas-painel.desktop \
      /usr/local/bin/cotas-admin
rm -rf /opt/cotas-impressao
if [ "${1:-}" = "--tudo" ]; then
  rm -rf /etc/cotas-impressao /var/lib/cotas-impressao
fi
echo "Desinstalado."
