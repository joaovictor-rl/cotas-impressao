"""Verifica se as impressoras estão respondendo. Usado pelo painel e pelo `cotas-admin diagnostico`."""
import os
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse

from . import banco, config

ESPERA_MAXIMA = 120  # segundos que um documento pode esperar na fila antes de virar alerta
DICA = "Verifique se ela está ligada e se o cabo está conectado."
_cache = {"quando": 0.0, "problemas": []}
_lock = threading.Lock()


def problemas_impressoras(validade=15):
    """Lista de {"impressora", "mensagem"}; o resultado é reaproveitado por alguns segundos."""
    with _lock:
        if time.time() - _cache["quando"] > validade:
            try:
                _cache["problemas"] = _problemas_windows() if config.WINDOWS else _problemas_linux()
            except Exception:
                _cache["problemas"] = []
            _cache["quando"] = time.time()
        return _cache["problemas"]


def _problema(nome, texto, dica=DICA):
    return {"impressora": nome, "mensagem": f"A impressora {nome} {texto}. {dica}"}


# ---------------------------------------------------------------- Linux (CUPS)

def _id_usb(uri):
    serial = parse_qs(urlparse(uri).query).get("serial")
    return serial[0] if serial else uri.split("?")[0]


def _usb_conectados():
    saida = subprocess.run(["lpinfo", "--include-schemes", "usb", "-v"],
                           capture_output=True, text=True, timeout=30).stdout
    return {_id_usb(linha.split()[1]) for linha in saida.splitlines() if " usb://" in linha}


def _problemas_linux():
    import cups
    from .backend_cups import PREFIXO

    conn = cups.Connection()
    agora = time.time()
    esperando = set()
    jobs = conn.getJobs(requested_attributes=["job-id", "job-printer-uri", "time-at-creation"])
    for job in jobs.values():
        if agora - job.get("time-at-creation", agora) > ESPERA_MAXIMA:
            esperando.add(job.get("job-printer-uri", "").rsplit("/", 1)[-1])

    usb = None
    problemas = []
    for nome, info in conn.getPrinters().items():
        uri = info.get("device-uri", "").removeprefix(PREFIXO)
        if uri.startswith("usb://"):
            usb = _usb_conectados() if usb is None else usb
            if _id_usb(uri) not in usb:
                problemas.append(_problema(nome, "não está conectada a este computador"))
                continue
        if info.get("printer-state") == 5:
            problemas.append(_problema(nome, "está parada", "Procure o suporte para reativá-la."))
        elif nome in esperando:
            problemas.append(_problema(nome, "não está recebendo os documentos"))
    return problemas


# ---------------------------------------------------------------- Windows

CONFIRA = "Confira a impressora ou procure o suporte."
PROBLEMAS_WINDOWS = {0x80: ("está desligada ou desconectada", DICA), 0x2: ("está com erro", CONFIRA),
                     0x8: ("está com papel preso", CONFIRA), 0x10: ("está sem papel", CONFIRA),
                     0x1000: ("não está disponível", DICA), 0x100000: ("precisa de atenção", CONFIRA)}
JOB_COM_ERRO = 0x2 | 0x20 | 0x40 | 0x400  # erro, offline, sem papel, intervenção


def _problemas_windows():
    import win32print

    ignoradas = {n.lower() for n in config.carregar()["impressoras_ignoradas"]}
    problemas = []
    for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL, None, 2):
        nome = p["pPrinterName"]
        if nome.lower() in ignoradas:
            continue
        problema = next((t for bit, t in PROBLEMAS_WINDOWS.items() if p["Status"] & bit), None)
        if not problema and p["cJobs"]:
            h = win32print.OpenPrinter(nome)
            try:
                if any(j["Status"] & JOB_COM_ERRO for j in win32print.EnumJobs(h, 0, 999, 1)):
                    problema = ("não está recebendo os documentos", DICA)
            finally:
                win32print.ClosePrinter(h)
        if problema:
            problemas.append(_problema(nome, *problema))
    return problemas


# ---------------------------------------------------------------- relatório completo

def relatorio():
    """Lista de (situacao, texto), com situacao "ok", "aviso" ou "erro"."""
    itens = []
    try:
        socket.create_connection(("127.0.0.1", config.carregar()["porta_api"]), timeout=2).close()
        itens.append(("ok", "Serviço de cotas rodando"))
    except OSError:
        itens.append(("erro", "Serviço de cotas parado: as impressões não estão sendo controladas"))

    estado = banco.estado()
    itens.append(("ok", "Controle ativo") if estado["controle_ativo"]
                 else ("aviso", "Controle DESATIVADO: impressões sem desconto"))
    if not estado["senha_definida"]:
        itens.append(("aviso", "Senha do administrador ainda não foi criada (abra o painel)"))

    if not config.WINDOWS:
        itens += _relatorio_linux()

    problemas = problemas_impressoras(validade=0)
    itens += [("erro", p["mensagem"]) for p in problemas]
    if not problemas:
        itens.append(("ok", "Nenhuma impressora com problema"))
    return itens


def _relatorio_linux():
    import cups
    from .backend_cups import PREFIXO

    pastas = ["/usr/lib/cups/backend", "/usr/libexec/cups/backend"]
    tem_backend = any(os.path.exists(os.path.join(p, "cotas")) for p in pastas)
    itens = []
    if any(os.path.exists(os.path.join(p, "tea4cups")) for p in pastas):
        itens.append(("aviso", "Tea4CUPS instalado: outro controle de cotas pode entrar em conflito"))

    for nome, info in cups.Connection().getPrinters().items():
        uri = info.get("device-uri", "")
        if not uri.startswith(PREFIXO):
            itens.append(("aviso", f"{nome}: ainda não passa pelo controle ({uri})"))
        elif not tem_backend:
            itens.append(("erro", f"{nome}: aponta para o controle, mas o backend 'cotas' não "
                                  "existe. Reinstale ou rode o desinstalar_linux.sh"))
        else:
            itens.append(("ok", f"{nome}: passa pelo controle ({uri.removeprefix(PREFIXO)})"))
    return itens
