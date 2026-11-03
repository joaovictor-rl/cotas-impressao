"""No Linux, quem decide cada trabalho é o backend (backend_cups.py).

Este monitor só garante que toda impressora, inclusive as instaladas depois,
tenha o endereço trocado para cotas:/... e apaga cópias da mesma impressora
(o sistema cria "Nome-2", "Nome-3"... quando não reconhece o endereço cotas:/).
"""
import logging
import subprocess
import time

import cups

from . import config
from .backend_cups import PREFIXO
from .restaurar import salvar_original

log = logging.getLogger("cotas.linux")


class Monitor:
    def envolver_impressoras(self, conn):
        ignoradas = config.carregar()["impressoras_ignoradas"]
        impressoras = conn.getPrinters()
        controladas = {i.get("device-uri") for i in impressoras.values()}
        for nome, info in impressoras.items():
            uri = info.get("device-uri", "")
            if nome in ignoradas or not uri or uri.startswith(PREFIXO):
                continue
            if PREFIXO + uri in controladas:
                if subprocess.run(["lpadmin", "-x", nome], capture_output=True).returncode == 0:
                    log.info("Cópia '%s' de uma impressora já controlada foi removida", nome)
                continue
            salvar_original(nome, uri)
            r = subprocess.run(["lpadmin", "-p", nome, "-v", PREFIXO + uri],
                               capture_output=True, text=True)
            if r.returncode == 0:
                controladas.add(PREFIXO + uri)
                log.info("Impressora '%s' agora passa pela cota", nome)
            else:
                log.warning("Não consegui ajustar '%s': %s", nome, r.stderr.strip())

    def executar(self):
        conn = None
        while True:
            try:
                conn = conn or cups.Connection()
                self.envolver_impressoras(conn)
            except Exception as e:
                log.warning("Erro ao falar com o CUPS (%s); tentando de novo...", e)
                conn = None
            time.sleep(10)
