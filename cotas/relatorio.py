"""Relatório de consumo por mês, também acessível pela rede (só leitura, com senha própria).

A porta da rede só lê: não dá para mudar cotas nem desligar o controle por ela.
"""
import base64
import datetime as dt
import io
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import banco, config

log = logging.getLogger("cotas.relatorio")
MAX_ERROS, ESPERA = 5, 60
_erros = {}  # ip -> (erros seguidos, bloqueado até)
_lock = threading.Lock()


def planilha_mes(mes):
    """Arquivo .xlsx com duas abas: resumo por usuário e cada impressão do mês."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    cabecalho = (Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="2563EB"))
    vermelho = Font(color="B91C1C")

    def aba(ws, titulos, larguras, linhas, centralizar):
        ws.append(titulos)
        for celula in ws[1]:
            celula.font, celula.fill = cabecalho
        for linha in linhas:
            ws.append(linha)
        for coluna in ws.iter_cols(min_col=1, max_col=len(titulos)):
            if coluna[0].column_letter in centralizar:
                for celula in coluna:
                    celula.alignment = Alignment(horizontal="center")
        for letra, largura in zip("ABCDEF", larguras):
            ws.column_dimensions[letra].width = largura
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    wb = Workbook()
    r = banco.relatorio(mes)
    resumo = wb.active
    resumo.title = "Resumo"
    aba(resumo, ["Usuário", "Folhas", "Impressões", "Bloqueadas"], [22, 10, 12, 12],
        [[u["usuario"], u["folhas"], u["impressoes"], u["bloqueios"]] for u in r["usuarios"]], "BCD")
    resumo.append(["Total"] + [sum(u[k] for u in r["usuarios"])
                               for k in ("folhas", "impressoes", "bloqueios")])
    for celula in resumo[resumo.max_row]:
        celula.font = Font(bold=True)
        if celula.column_letter != "A":
            celula.alignment = Alignment(horizontal="center")

    impressoes = wb.create_sheet("Impressões")
    aba(impressoes, ["Data", "Hora", "Usuário", "Impressora", "Folhas", "Situação"],
        [12, 8, 22, 22, 8, 12],
        [[dt.date.fromisoformat(t["data"]), dt.time.fromisoformat(t["hora"]), t["usuario"],
          t["impressora"], t["folhas"], t["situacao"]] for t in banco.trabalhos_mes(mes)], "ABE")
    for data, hora, *_, situacao in impressoes.iter_rows(min_row=2):
        data.number_format, hora.number_format = "DD/MM/YYYY", "HH:MM"
        if situacao.value == "bloqueado":
            situacao.font = vermelho

    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MODELO = Path(__file__).with_name("relatorio.html")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def nome_mes(mes):
    ano, m = mes.split("-")
    return f"{MESES[int(m) - 1].capitalize()} de {ano}"


def pagina_html(mes):
    r = banco.relatorio(mes)
    cfg = config.carregar()
    proprios = banco.limites_proprios()
    isentos = {banco.normalizar(u) for u in cfg["usuarios_isentos"]}

    ano, m = map(int, mes.split("-"))
    folhas_dia = {d["data"]: d["folhas"] for d in r["dias"]}
    dia, dias = dt.date(ano, m, 1), []
    while dia.month == m and dia <= dt.date.today():
        dias.append([dia.strftime("%d/%m"), folhas_dia.get(dia.isoformat(), 0)])
        dia += dt.timedelta(days=1)

    meses = sorted(set(banco.meses()) | {mes, dt.date.today().strftime("%Y-%m")}, reverse=True)
    dados = {
        "mes": mes, "nome_mes": nome_mes(mes), "dias": dias,
        "meses": [[x, nome_mes(x)] for x in meses],
        "usuarios": [[u["usuario"], u["folhas"], u["impressoes"], u["bloqueios"],
                      "isento" if u["usuario"] in isentos
                      else proprios.get(u["usuario"], cfg["limite_diario_padrao"])]
                     for u in r["usuarios"]],
    }
    # "</" escapado: um nome de usuário não consegue fechar a tag <script>
    return MODELO.read_text(encoding="utf-8").replace(
        "/*DADOS*/null", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"))


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
            self.enviar(200, "text/html", pagina_html(mes))
        elif url.path == "/relatorio.xlsx":
            self.enviar(200, XLSX, planilha_mes(mes), f"relatorio_{mes}.xlsx")
        else:
            self.enviar(404, "text/plain", "Não encontrado.")

    def enviar(self, codigo, tipo, conteudo, arquivo=None):
        corpo = conteudo if isinstance(conteudo, bytes) else conteudo.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo if arquivo else f"{tipo}; charset=utf-8")
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
