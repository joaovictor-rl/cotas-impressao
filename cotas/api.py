"""API local (só 127.0.0.1) usada pelo painel. Mudanças exigem a senha do administrador."""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import banco, config, diagnostico

log = logging.getLogger("cotas.api")

MAX_ERROS, ESPERA = 5, 60  # 5 senhas erradas seguidas bloqueiam por 60 s
_tentativas = {"erros": 0, "ate": 0.0}
_lock = threading.Lock()


class Erro(Exception):
    def __init__(self, codigo, msg):
        super().__init__(msg)
        self.codigo = codigo


def conferir_senha(senha):
    if not banco.estado()["senha_definida"]:
        raise Erro(409, "A senha do administrador ainda não foi criada. "
                        "Peça ao suporte para rodar: sudo cotas-admin senha")
    with _lock:
        espera = int(_tentativas["ate"] - time.time())
        if espera > 0:
            raise Erro(429, f"Muitas tentativas erradas. Aguarde {espera} segundos.")
        if banco.conferir_senha(senha):
            _tentativas["erros"] = 0
            return
        _tentativas["erros"] += 1
        if _tentativas["erros"] >= MAX_ERROS:
            _tentativas.update(erros=0, ate=time.time() + ESPERA)
    log.warning("Senha errada no painel")
    raise Erro(403, "Senha incorreta.")


def dados_admin():
    return dict(banco.estado(), limite_padrao=config.carregar()["limite_diario_padrao"],
                limites_proprios=banco.limites_proprios())


def mudar_controle(dados):
    banco.definir_controle(bool(dados.get("ativo")))
    log.info("Controle %s pelo painel", "ativado" if dados.get("ativo") else "desativado")
    return dados_admin()


def mudar_limite(dados):
    usuario = banco.normalizar(dados.get("usuario"))
    if usuario and dados.get("usar_padrao"):
        banco.remover_limite(usuario)
        return dados_admin()
    try:
        limite = int(dados.get("limite"))
    except (TypeError, ValueError):
        raise Erro(400, "Digite um número de folhas.")
    if not 0 <= limite <= 1000:
        raise Erro(400, "Use um número entre 0 e 1000.")
    if usuario:
        banco.definir_limite(usuario, limite)
    else:
        cfg = config.carregar()
        cfg["limite_diario_padrao"] = limite
        config.salvar(cfg)
    log.info("Limite de %s = %s folha(s) pelo painel", usuario or "todos", limite)
    return dados_admin()


ACOES_ADMIN = {"/login": lambda d: dados_admin(), "/controle": mudar_controle,
               "/limite": mudar_limite}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        usuario = parse_qs(url.query).get("usuario", [""])[0]
        if url.path != "/saldo" or not usuario:
            return self.responder(404, {"erro": "não encontrado"})
        dados = banco.saldo(usuario)
        dados.update(banco.estado(), trabalhos=banco.ultimos(usuario),
                     impressoras_com_problema=diagnostico.problemas_impressoras())
        self.responder(200, dados)

    def do_POST(self):
        try:
            tamanho = min(int(self.headers.get("Content-Length") or 0), 4096)
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
            if self.path not in ACOES_ADMIN:
                raise Erro(404, "não encontrado")
            conferir_senha(str(dados.get("senha", "")))
            self.responder(200, ACOES_ADMIN[self.path](dados))
        except Erro as e:
            self.responder(e.codigo, {"erro": str(e)})
        except Exception as e:
            log.exception("Erro na API")
            self.responder(500, {"erro": str(e)})

    def responder(self, codigo, dados):
        corpo = json.dumps(dados, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True
    # No Windows, reaproveitar a porta deixaria rodar dois serviços ao mesmo tempo
    allow_reuse_address = not config.WINDOWS


def iniciar(porta):
    srv = _Servidor(("127.0.0.1", porta), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("API ouvindo em 127.0.0.1:%s", porta)
