"""Verifica se as impressoras estão respondendo. Usado pelo painel e pelo `cotas-admin diagnostico`."""
import os
import socket
import threading
import time
from pathlib import Path
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
                _cache["problemas"] = _problemas_linux()
            except Exception:
                _cache["problemas"] = []
            _cache["quando"] = time.time()
        return _cache["problemas"]


def _problema(nome, texto, dica=DICA):
    return {"impressora": nome, "mensagem": f"A impressora {nome} {texto}. {dica}"}


# ---------------------------------------------------------------- Linux (CUPS)

def usb_conectada(uri):
    """Procura o número de série da impressora em /sys, sem tocar no aparelho.

    Não usar "lpinfo" aqui: a busca do CUPS desconecta o driver USB da impressora
    e, repetida a cada poucos segundos, reinicia a porta USB no meio da impressão.
    Devolve None quando o endereço não tem número de série (não dá para saber).
    """
    serial = parse_qs(urlparse(uri).query).get("serial")
    if not serial:
        return None
    for arquivo in Path("/sys/bus/usb/devices").glob("*/serial"):
        try:
            if arquivo.read_text().strip() == serial[0]:
                return True
        except OSError:
            continue
    return False


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

    problemas = []
    for nome, info in conn.getPrinters().items():
        uri = info.get("device-uri", "").removeprefix(PREFIXO)
        if uri.startswith("usb://"):
            if usb_conectada(uri) is False:
                problemas.append(_problema(nome, "não está conectada a este computador",
                                           DICA + " Os documentos aguardam na fila e só "
                                           "descontam da cota quando forem impressos."))
                continue
        if info.get("printer-state") == 5:
            problemas.append(_problema(nome, "está parada", "Procure o suporte para reativá-la."))
        elif nome in esperando:
            problemas.append(_problema(nome, "não está recebendo os documentos"))
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
    for outro in ("tea4cups", "pykota"):
        if any(os.path.exists(os.path.join(p, outro)) for p in pastas):
            itens.append(("aviso", f"{outro} instalado: outro controle de cotas pode entrar em conflito"))

    for nome, info in cups.Connection().getPrinters().items():
        uri = info.get("device-uri", "")
        if not uri.startswith(PREFIXO):
            itens.append(("aviso", f"{nome}: ainda não passa pelo controle ({uri})"))
        elif not tem_backend:
            itens.append(("erro", f"{nome}: aponta para o controle, mas o backend 'cotas' não "
                                  "existe. Rode o REPARAR_LINUX.sh"))
        else:
            itens.append(("ok", f"{nome}: passa pelo controle ({uri.removeprefix(PREFIXO)})"))
    return itens
