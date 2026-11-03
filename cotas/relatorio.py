"""Relatório de consumo por mês, também acessível pela rede (só leitura, com senha própria).

A porta da rede só lê: não dá para mudar cotas nem desligar o controle por ela.
"""
import base64
import csv
import datetime as dt
import html
import io
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import banco

log = logging.getLogger("cotas.relatorio")
MAX_ERROS, ESPERA = 5, 60
_erros = {}  # ip -> (erros seguidos, bloqueado até)
_lock = threading.Lock()


def csv_mes(mes):
    """Planilha para o Excel em português: separador ";" e datas dd/mm/aaaa."""
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["data", "hora", "usuario", "impressora", "folhas", "situacao"])
    for t in banco.trabalhos_mes(mes):
        a, m, d = t["data"].split("-")
        w.writerow([f"{d}/{m}/{a}", t["hora"][:5], t["usuario"], t["impressora"],
                    t["folhas"], t["situacao"]])
    return "\ufeff" + saida.getvalue()  # BOM: o Excel reconhece os acentos


def pagina_html(r):
    linhas = "".join(f"<tr><td>{html.escape(u['usuario'])}</td><td>{u['folhas']}</td>"
                     f"<td>{u['impressoes']}</td><td>{u['bloqueios']}</td></tr>"
                     for u in r["usuarios"])
    return (f"<!doctype html><meta charset=utf-8><title>Relatório {r['mes']}</title>"
            f"<h1>Relatório de impressão · {r['mes']}</h1>"
            f"<p><a href='relatorio.csv?mes={r['mes']}'>Baixar planilha (CSV)</a></p>"
            "<table border=1 cellpadding=6><tr><th>Usuário</th><th>Folhas</th>"
            f"<th>Impressões</th><th>Bloqueadas</th></tr>{linhas}</table>")


def _senha_ok(ip, cabecalho):
    with _lock:
        erros, ate = _erros.get(ip, (0, 0.0))
        if time.time() < ate:
            return False
        try:
            senha = base64.b64decode(cabecalho.split(" ", 1)[1]).decode().split(":", 1)[1]
        except (IndexError, ValueError):
            return False
        if banco.conferir_senha(senha, "senha_relatorio"):
            _erros.pop(ip, None)
            return True
        erros += 1
        _erros[ip] = (0, time.time() + ESPERA) if erros >= MAX_ERROS else (erros, 0.0)
    log.warning("Senha errada no relatório vinda de %s", ip)
    return False


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _senha_ok(self.client_address[0], self.headers.get("Authorization", "")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Cotas de impressao", charset="UTF-8"')
            self.end_headers()
            return
        url = urlparse(self.path)
        mes = parse_qs(url.query).get("mes", [dt.date.today().strftime("%Y-%m")])[0]
        if not re.fullmatch(r"\d{4}-\d{2}", mes):
            return self.enviar(400, "text/plain", "Mês inválido (use AAAA-MM).")
        if url.path in ("/", "/relatorio"):
            self.enviar(200, "text/html", pagina_html(banco.relatorio(mes)))
        elif url.path == "/relatorio.csv":
            self.enviar(200, "text/csv", csv_mes(mes), f"relatorio_{mes}.csv")
        else:
            self.enviar(404, "text/plain", "Não encontrado.")

    def enviar(self, codigo, tipo, texto, arquivo=None):
        corpo = texto.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if arquivo:
            self.send_header("Content-Disposition", f'attachment; filename="{arquivo}"')
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass


def iniciar(porta):
    srv = ThreadingHTTPServer(("0.0.0.0", porta), _Handler)  # 0.0.0.0: aceita conexões da rede
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Relatórios na rede pela porta %s", porta)
