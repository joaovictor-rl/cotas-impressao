#!/usr/bin/env bash
# Instala o controle de cotas de impressão no Linux (Zorin, Ubuntu, Mint, Debian, Fedora).
# Uso:  bash INSTALAR_LINUX.sh      (pede a senha de administrador sozinho)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Precisa de permissão de administrador; digite sua senha:"
  exec sudo bash "$(readlink -f "$0")" "$@"
fi

ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO=/opt/cotas-impressao

# Outro controle de cotas junto com este pode bloquear ou contar impressões em dobro
for outro in tea4cups pykota; do
  if [ -e /usr/lib/cups/backend/$outro ] || [ -e /usr/libexec/cups/backend/$outro ]; then
    echo "ATENÇÃO: $outro (outro controle de cotas) está instalado neste computador."
    echo "Os dois juntos podem bloquear ou contar as impressões em dobro."
    read -r -p "Continuar mesmo assim? [s/N] " resposta || resposta=""
    [[ "$resposta" =~ ^[sS]$ ]] || { echo "Instalação cancelada."; exit 1; }
  fi
done

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

# A senha do painel é criada aqui, por quem instala; o painel nunca oferece criar
echo ">> Senha do administrador do painel"
if (cd "$DESTINO" && python3 -c "from cotas import banco; exit(not banco.estado()['senha_definida'])"); then
  echo "   Já existe uma senha (para trocar: sudo cotas-admin senha)."
elif [ -t 0 ]; then
  until cotas-admin senha; do echo "   Tente de novo."; done
else
  echo "   ATENÇÃO: nenhuma senha criada. Rode: sudo cotas-admin senha"
fi

echo
echo "Pronto! Limite padrão: $(python3 -c "import json;print(json.load(open('/etc/cotas-impressao/config.json'))['limite_diario_padrao'])") folhas/dia."
echo "  Ver saldos:        sudo cotas-admin listar"
echo "  Mudar o padrão:    sudo cotas-admin padrao 20"
echo "  Log do serviço:    journalctl -u cotas-impressao -f"
echo "O painel abre no login; o ícone 'Cota de Impressão' aparece na Área de Trabalho"
echo "de cada usuário. Cada usuário tem a sua própria cota."
echo "Trocar a senha do administrador:  sudo cotas-admin senha"
