#!/usr/bin/env bash
# Instala o controle de cotas de impressão no Linux (Zorin, Ubuntu, Mint, Debian, Fedora).
# Uso:  bash instalar_linux.sh      (pede a senha de administrador sozinho)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Precisa de permissão de administrador; digite sua senha:"
  exec sudo bash "$(readlink -f "$0")" "$@"
fi

ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO=/opt/cotas-impressao

echo ">> Instalando dependências..."
if command -v apt-get >/dev/null; then
  apt-get update -qq
  apt-get install -y python3 python3-tk python3-cups poppler-utils ghostscript cups
elif command -v dnf >/dev/null; then
  dnf install -y python3 python3-tkinter python3-cups poppler-utils ghostscript cups
else
  echo "Instale manualmente: python3, tkinter, pycups, poppler-utils, ghostscript"; exit 1
fi

echo ">> Copiando arquivos para $DESTINO..."
install -d "$DESTINO"
cp -r "$ORIGEM/cotas" "$ORIGEM/servico.py" "$ORIGEM/painel.py" "$ORIGEM/admin.py" "$DESTINO/"
chmod -R a+rX "$DESTINO"

echo ">> Criando configuração..."
install -d -m 755 /etc/cotas-impressao /var/lib/cotas-impressao
( cd "$DESTINO" && python3 -c "from cotas import config, banco; config.garantir_pastas(); banco.inicializar()" )
chmod 644 /etc/cotas-impressao/config.json

echo ">> Instalando o backend 'cotas' no CUPS..."
BACKENDS=""
for d in /usr/lib/cups/backend /usr/libexec/cups/backend; do
  [ -d "$d" ] && BACKENDS="$d" && break
done
[ -n "$BACKENDS" ] || { echo "Pasta de backends do CUPS não encontrada"; exit 1; }
install -m 700 "$ORIGEM/backend_cotas" "$BACKENDS/cotas"
chown root:root "$BACKENDS/cotas"
chmod 700 "$BACKENDS/cotas"   # 0700 = o CUPS roda como root (precisa ler a fila)

echo ">> Criando o serviço (systemd)..."
cat > /etc/systemd/system/cotas-impressao.service <<EOF
[Unit]
Description=Cotas de impressao por usuario
After=cups.service
Wants=cups.service

[Service]
ExecStart=/usr/bin/python3 $DESTINO/servico.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now cotas-impressao.service

echo ">> Painel: abrir no login de todos os usuários e no menu..."
install -d /usr/share/applications /etc/xdg/autostart
cat > /usr/share/applications/cotas-painel.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Cota de Impressão
Comment=Veja quantas folhas você ainda pode imprimir hoje
Exec=/usr/bin/python3 $DESTINO/painel.py
Icon=printer
Terminal=false
Categories=Utility;
EOF
sed 's#painel.py#painel.py --minimizado#' /usr/share/applications/cotas-painel.desktop \
  > /etc/xdg/autostart/cotas-painel.desktop

echo ">> Comando do administrador: cotas-admin"
cat > /usr/local/bin/cotas-admin <<EOF
#!/bin/sh
cd $DESTINO && exec /usr/bin/python3 admin.py "\$@"
EOF
chmod 755 /usr/local/bin/cotas-admin

echo
echo "Pronto! Limite padrão: $(python3 -c "import json;print(json.load(open('/etc/cotas-impressao/config.json'))['limite_diario_padrao'])") folhas/dia."
echo "  Ver saldos:        sudo cotas-admin listar"
echo "  Mudar o padrão:    sudo cotas-admin padrao 20"
echo "  Log do serviço:    journalctl -u cotas-impressao -f"
echo "O painel abre no login; o ícone 'Cota de Impressão' aparece na Área de Trabalho"
echo "de cada usuário. Na primeira abertura ele pede para criar a senha do administrador."
echo "Esqueceu a senha?  sudo cotas-admin senha"
