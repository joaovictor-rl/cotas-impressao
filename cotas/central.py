"""Servidor central: recebe os registros dos PCs com cotas e mostra os relatórios.

Não controla impressoras. Uso (o instalador cria o comando cotas-servidor):
  cotas-servidor            roda o servidor (o systemd faz isso)
  cotas-servidor chave      gera uma nova chave para os PCs enviarem registros
  cotas-servidor senha      cria ou troca a senha da página de relatórios
  cotas-servidor backup     copia o banco para a pasta backups (guarda as 8 últimas)
"""
import contextlib
import datetime as dt
import getpass
import logging
import secrets
import sqlite3
import sys
import threading

from . import banco, config, relatorio
from .servico import configurar_log

MANTER_BACKUPS = 8


def nova_chave():
    chave = secrets.token_urlsafe(24)
    banco.gravar_estado("chave_central", relatorio.hash_chave(chave))
    print("Chave do servidor (guarde: ela não é mostrada de novo):\n")
    print(f"    {chave}\n")
    print("No PC das cotas:  sudo cotas-admin servidor IP-DESTE-SERVIDOR " + chave)


def nova_senha():
    senha = getpass.getpass("Senha da página de relatórios: ")
    if len(senha) < 4 or getpass.getpass("Repita a senha: ") != senha:
        sys.exit("Senha inválida (mínimo 4 caracteres) ou as duas não conferem.")
    banco.definir_senha(senha, substituir=True, chave="senha_relatorio")
    print("Senha definida.")


def backup():
    pasta = config.PASTA_DADOS / "backups"
    pasta.mkdir(mode=0o700, exist_ok=True)
    destino = pasta / f"cotas-{dt.date.today().isoformat()}.db"
    with contextlib.closing(sqlite3.connect(config.ARQ_BANCO)) as origem, \
         contextlib.closing(sqlite3.connect(destino)) as copia:
        origem.backup(copia)  # cópia consistente mesmo com o servidor gravando
    for antigo in sorted(pasta.glob("cotas-*.db"))[:-MANTER_BACKUPS]:
        antigo.unlink()
    print(f"Backup salvo em {destino}")


def main(argv):
    config.garantir_pastas()
    banco.inicializar()
    acoes = {"chave": nova_chave, "senha": nova_senha, "backup": backup}
    if argv:
        if argv[0] not in acoes:
            sys.exit(__doc__)
        return acoes[argv[0]]()
    configurar_log()
    relatorio.iniciar(config.carregar()["porta_relatorio"])
    logging.info("Servidor central iniciado")
    threading.Event().wait()  # o servidor HTTP roda em outra thread
