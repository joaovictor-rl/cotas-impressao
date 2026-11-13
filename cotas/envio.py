"""Conversa com o servidor central a cada minuto.

Envia os registros de impressão novos ou alterados e busca as alterações de cota
pedidas pelo site do servidor. É sempre este PC que se conecta (nenhuma porta aberta
aqui), e as cotas continuam sendo decididas neste PC: se o servidor ou a rede cair,
nada para; tudo espera a próxima conversa.
"""
import json
import logging
import os
import pwd
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


def _pedido(servidor, caminho, corpo=None):
    pedido = urllib.request.Request(
        servidor["url"].rstrip("/") + caminho, data=corpo, method="POST" if corpo else "GET",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + servidor["chave"]})
    with urllib.request.urlopen(pedido, timeout=20) as resposta:
        return resposta.read()


def usuarios_do_sistema():
    """Contas de pessoas neste PC (UID 1000+, com login), mesmo as que nunca imprimiram."""
    return sorted({banco.normalizar(p.pw_name) for p in pwd.getpwall()
                   if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false"))})


def enviar_pendentes(servidor, forcar=False):
    """Envia tudo o que falta, em lotes. Devolve quantos registros foram enviados.

    forcar=True manda pelo menos uma vez (mesmo vazio) para atualizar os limites no servidor.
    """
    enviados = 0
    while True:
        cfg = config.carregar()
        desde = int(banco.ler_estado("enviado_ate", -1))
        registros = banco.pendentes(desde)
        if not registros and not forcar:
            return enviados
        forcar = False
        corpo = json.dumps({
            "origem": socket.gethostname(),
            "registros": registros,
            "limites": {"padrao": cfg["limite_diario_padrao"], "isentos": cfg["usuarios_isentos"],
                        "proprios": banco.limites_proprios(),
                        "controle": banco.estado()["controle_ativo"],
                        "usuarios": usuarios_do_sistema()},
        }).encode()
        _pedido(servidor, "/registros", corpo)
        if not registros:
            return enviados
        banco.gravar_estado("enviado_ate", registros[-1]["versao"])
        enviados += len(registros)


def aplicar(alteracao):
    acao, usuario, valor = alteracao["acao"], alteracao["usuario"], alteracao["valor"]
    if acao == "padrao":
        cfg = config.carregar()
        cfg["limite_diario_padrao"] = int(valor)
        config.salvar(cfg)
    elif acao == "limite":
        banco.definir_limite(usuario, valor)
    elif acao == "remover_limite":
        banco.remover_limite(usuario)
    elif acao == "extra":
        banco.adicionar_extra(usuario, valor)
    elif acao == "controle":
        banco.definir_controle(bool(valor))
    else:
        raise ValueError(f"ação desconhecida: {acao}")
    log.info("Alteração do servidor aplicada: %s %s %s", acao, usuario or "", "" if valor is None else valor)


def buscar_alteracoes(servidor):
    """Aplica as alterações pedidas pelo site. Devolve quantas foram aplicadas."""
    feitas = int(banco.ler_estado("alteracao_ate", 0))
    alteracoes = json.loads(_pedido(servidor, f"/alteracoes?desde={feitas}"))
    for alteracao in alteracoes:
        aplicar(alteracao)
        banco.gravar_estado("alteracao_ate", alteracao["id"])
    if alteracoes:  # confirma ao servidor e atualiza lá os limites mostrados no site
        _pedido(servidor, f"/alteracoes?desde={alteracoes[-1]['id']}")
        enviar_pendentes(servidor, forcar=True)
    return len(alteracoes)


def _laco():
    falhando = False
    primeira = True  # ao iniciar, manda a situação atual (usuários, limites) mesmo sem impressão nova
    while True:
        servidor = carregar()
        if servidor:
            try:
                n = enviar_pendentes(servidor, forcar=primeira)
                primeira = False
                if n or falhando:
                    log.info("Servidor: %s registro(s) enviados", n)
                buscar_alteracoes(servidor)
                falhando = False
            except (OSError, urllib.error.URLError, ValueError, KeyError) as e:
                if not falhando:  # avisa uma vez, não a cada minuto
                    log.warning("Servidor indisponível (%s); os registros esperam o próximo envio", e)
                falhando = True
        time.sleep(INTERVALO)


def iniciar():
    threading.Thread(target=_laco, daemon=True).start()
