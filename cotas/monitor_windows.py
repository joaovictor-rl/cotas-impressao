"""No Windows, o serviço vigia a fila do spooler:

1. Toda impressora local só começa a imprimir depois que o documento está completo.
2. Cada trabalho novo é pausado assim que aparece.
3. Com o documento pronto, o serviço conta as folhas e retoma ou exclui o trabalho.
"""
import logging
import time

import pywintypes
import win32print

from . import banco, config, contador

log = logging.getLogger("cotas.windows")

PAUSAR, RETOMAR, EXCLUIR = 1, 2, 5
PAUSADO, GERANDO = 0x1, 0x8
TERMINADO = 0x4 | 0x80 | 0x100 | 0x1000  # excluindo, impresso, excluído, completo
ACESSO_TOTAL = 0x000F000C
SO_APOS_SPOOL = 0x1  # PRINTER_ATTRIBUTE_QUEUED
DM_COPIAS, DM_DUPLEX = 0x100, 0x1000


def abrir(nome):
    return win32print.OpenPrinter(nome, {"DesiredAccess": ACESSO_TOTAL})


def folhas_do_trabalho(job):
    paginas = job.get("TotalPages") or 0
    if paginas <= 0:
        return None
    dm = job.get("pDevMode")
    copias = dm.Copies if dm and dm.Fields & DM_COPIAS and dm.Copies > 0 else 1
    duplex = bool(dm and dm.Fields & DM_DUPLEX and dm.Duplex != 1)
    return contador.folhas(paginas, copias, frente_verso=duplex)


class Monitor:
    def __init__(self):
        self.decididos = {}   # chave -> liberado?
        self.sem_paginas = {}  # chave -> quando o documento ficou pronto sem páginas
        self.ajustadas = set()

    def ajustar(self, nome):
        h = abrir(nome)
        try:
            info = win32print.GetPrinter(h, 2)
            if not info["Attributes"] & SO_APOS_SPOOL:
                info["Attributes"] |= SO_APOS_SPOOL
                info["pSecurityDescriptor"] = None
                win32print.SetPrinter(h, 2, info, 0)
        finally:
            win32print.ClosePrinter(h)

    def tratar(self, h, nome, job, cfg):
        if job["Status"] & TERMINADO:
            return
        jid = job["JobId"]
        chave = f"win-{nome}-{jid}-{job.get('Submitted')}"

        if chave not in self.decididos and (sit := banco.situacao(chave)):
            self.decididos[chave] = sit in banco.LIBERADAS
        if chave in self.decididos:
            if not self.decididos[chave]:
                win32print.SetJob(h, jid, 0, None, EXCLUIR)
            return

        # Ainda não decidido: mantém pausado (mesmo se o usuário tentar retomar)
        if not job["Status"] & PAUSADO:
            win32print.SetJob(h, jid, 0, None, PAUSAR)
        if job["Status"] & GERANDO:
            return

        folhas = folhas_do_trabalho(job)
        # Alguns drivers demoram a informar as páginas: espera até 5 s
        if folhas is None and time.time() - self.sem_paginas.setdefault(chave, time.time()) < 5:
            return
        self.sem_paginas.pop(chave, None)

        usuario = job.get("pUserName") or "desconhecido"
        liberar, sit, motivo = banco.decidir(chave, usuario, nome, job.get("pDocument"),
                                             folhas, cfg)
        self.decididos[chave] = liberar
        win32print.SetJob(h, jid, 0, None, RETOMAR if liberar else EXCLUIR)
        log.info("%s %s #%s %s: %s folha(s) %s", sit.upper(), nome, jid, usuario, folhas, motivo)

    def ciclo(self):
        cfg = config.carregar()
        ignoradas = {n.lower() for n in cfg["impressoras_ignoradas"]}
        for _, _, nome, _ in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL, None, 1):
            if nome.lower() in ignoradas:
                continue
            try:
                if nome not in self.ajustadas:
                    self.ajustadas.add(nome)
                    self.ajustar(nome)
                h = abrir(nome)
            except pywintypes.error as e:
                log.warning("Impressora '%s': %s", nome, e)
                continue
            try:
                for job in win32print.EnumJobs(h, 0, 999, 2):
                    try:
                        self.tratar(h, nome, job, cfg)
                    except pywintypes.error as e:
                        log.warning("%s #%s: %s", nome, job.get("JobId"), e)
            finally:
                win32print.ClosePrinter(h)
        if len(self.decididos) > 5000:
            self.decididos.clear()

    def executar(self):
        while True:
            try:
                self.ciclo()
            except Exception:
                log.exception("Erro no monitor")
                time.sleep(3)
            time.sleep(0.5)
