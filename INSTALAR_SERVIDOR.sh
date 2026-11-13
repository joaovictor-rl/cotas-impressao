#!/usr/bin/env bash
# Instala o servidor central (Ubuntu Server ou qualquer Linux com systemd):
# recebe os registros dos PCs com cotas e mostra os relatórios mesmo com eles desligados.
# Uso:  bash INSTALAR_SERVIDOR.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Precisa de permissão de administrador; digite sua senha:"
  exec sudo bash "$(readlink -f "$0")" "$@"
fi

ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO=/opt/cotas-servidor
AMBIENTE="COTAS_CONFIG_DIR=/etc/cotas-servidor COTAS_DATA_DIR=/var/lib/cotas-servidor"

echo ">> Instalando dependências..."
if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y python3 python3-openpyxl
elif command -v dnf >/dev/null; then
  dnf install -y python3 python3-openpyxl
fi

echo ">> Copiando o programa para $DESTINO..."
install -d "$DESTINO"
cp -r "$ORIGEM/cotas" "$ORIGEM/servidor_central.py" "$DESTINO/"
chmod -R a+rX "$DESTINO"
install -d -m 755 /etc/cotas-servidor
install -d -m 700 /var/lib/cotas-servidor   # registros de uso: só o root lê

cat > /usr/local/bin/cotas-servidor <<EOF
#!/bin/sh
exec env $AMBIENTE python3 $DESTINO/servidor_central.py "\$@"
EOF
chmod 755 /usr/local/bin/cotas-servidor

echo ">> Criando o serviço e o backup semanal (systemd)..."
cat > /etc/systemd/system/cotas-servidor.service <<EOF
[Unit]
Description=Servidor central das cotas de impressao
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/cotas-servidor
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/cotas-servidor-backup.service <<EOF
[Unit]
Description=Backup do banco do servidor de cotas

[Service]
Type=oneshot
ExecStart=/usr/local/bin/cotas-servidor backup
EOF
cat > /etc/systemd/system/cotas-servidor-backup.timer <<EOF
[Unit]
Description=Backup semanal do servidor de cotas

[Timer]
OnCalendar=weekly
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cotas-servidor-backup.timer
systemctl enable cotas-servidor.service
systemctl restart cotas-servidor.service

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 8632/tcp >/dev/null && echo ">> Firewall: porta 8632 liberada."
fi

cd "$DESTINO"
if ! env $AMBIENTE python3 -c "from cotas import banco, config; config.garantir_pastas(); banco.inicializar(); exit(0 if banco.ler_estado('senha_relatorio') else 1)"; then
  echo ">> Crie a senha da página de relatórios:"
  until cotas-servidor senha; do echo "   Tente de novo."; done
fi
if ! env $AMBIENTE python3 -c "from cotas import banco; exit(0 if banco.ler_estado('chave_central') else 1)"; then
  echo
  cotas-servidor chave
else
  echo ">> A chave já existe (para gerar outra: sudo cotas-servidor chave)."
fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "Pronto! Relatórios em:  http://$IP:8632/relatorio"
echo "Backup semanal em /var/lib/cotas-servidor/backups (as 8 últimas cópias)."
