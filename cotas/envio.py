"""Envia ao servidor central os registros de impressão novos ou alterados.

As cotas continuam sendo decididas neste PC: se o servidor ou a rede cair,
nada para; os registros esperam e vão no envio seguinte.
"""
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request

from . import banco, config

log = logging.getLogger("cotas.envio")
INTERVALO = 60


def carregar():
    """{"url": ..., "chave": ...} ou None. Arquivo só do root: a chave é secreta."""
    try:
        return json.loads(config.ARQ_SERVIDOR.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def salvar(url, chave):
    config.PASTA_CONFIG.mkdir(parents=True, exist_ok=True)
    fd = os.open(config.ARQ_SERVIDOR, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"url": url, "chave": chave}, f)


def enviar_pendentes(servidor):
    """Envia tudo o que falta, em lotes. Devolve quantos registros foram enviados."""
    cfg = config.carregar()
    enviados = 0
    while True:
        desde = int(banco.ler_estado("enviado_ate", -1))
        registros = banco.pendentes(desde)
        if not registros:
            return enviados
        corpo = json.dumps({
            "origem": socket.gethostname(),
            "registros": registros,
            "limites": {"padrao": cfg["limite_diario_padrao"], "isentos": cfg["usuarios_isentos"],
                        "proprios": banco.limites_proprios()},
        }).encode()
        pedido = urllib.request.Request(
            servidor["url"].rstrip("/") + "/registros", data=corpo, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + servidor["chave"]})
        with urllib.request.urlopen(pedido, timeout=20):
            pass
        banco.gravar_estado("enviado_ate", registros[-1]["versao"])
        enviados += len(registros)


def _laco():
    falhando = False
    while True:
        servidor = carregar()
        if servidor:
            try:
                n = enviar_pendentes(servidor)
                if n or falhando:
                    log.info("Servidor: %s registro(s) enviados", n)
                falhando = False
            except (OSError, urllib.error.URLError, ValueError) as e:
                if not falhando:  # avisa uma vez, não a cada minuto
                    log.warning("Servidor indisponível (%s); os registros esperam o próximo envio", e)
                falhando = True
        time.sleep(INTERVALO)


def iniciar():
    threading.Thread(target=_laco, daemon=True).start()
