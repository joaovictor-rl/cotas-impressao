"""Relatório de consumo por mês, também acessível pela rede (só leitura, com senha própria).

A porta da rede só lê: não dá para mudar cotas nem desligar o controle por ela.
"""
import base64
import datetime as dt
import hashlib
import hmac
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
    # No servidor central, os limites são os que o PC das cotas mandou junto com os registros
    remotos = json.loads(banco.ler_estado("limites_remotos", "null"))
    padrao = remotos["padrao"] if remotos else cfg["limite_diario_padrao"]
    proprios = remotos["proprios"] if remotos else banco.limites_proprios()
    isentos = {banco.normalizar(u) for u in (remotos["isentos"] if remotos else cfg["usuarios_isentos"])}

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
                      else proprios.get(u["usuario"], padrao)]
                     for u in r["usuarios"]],
    }
    # "</" escapado: um nome de usuário não consegue fechar a tag <script>
    return MODELO.read_text(encoding="utf-8").replace(
        "/*DADOS*/null", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"))


def _tentativa(ip, conferir):
    """Depois de MAX_ERROS erros seguidos, o IP espera ESPERA segundos."""
    with _lock:
        erros, ate = _erros.get(ip, (0, 0.0))
        if time.time() < ate:
            return False
        if conferir():
            _erros.pop(ip, None)
            return True
        erros += 1
        _erros[ip] = (0, time.time() + ESPERA) if erros >= MAX_ERROS else (erros, 0.0)
    log.warning("Senha ou chave errada vinda de %s", ip)
    return False


def _senha_ok(ip, cabecalho):
    try:
        senha = base64.b64decode(cabecalho.split(" ", 1)[1]).decode().split(":", 1)[1]
    except (IndexError, ValueError):
        return False  # navegador ainda não mandou senha: não conta como erro
    return _tentativa(ip, lambda: banco.conferir_senha(senha, "senha_relatorio"))


def hash_chave(chave):
    return hashlib.sha256(chave.encode()).hexdigest()


def _chave_ok(ip, cabecalho):
    guardada = banco.ler_estado("chave_central")
    if not guardada or not cabecalho.startswith("Bearer "):
        return False
    return _tentativa(ip, lambda: hmac.compare_digest(hash_chave(cabecalho[7:]), guardada))


def _validar(dados):
    """Só aceita registros no formato esperado; qualquer coisa estranha recusa o lote inteiro."""
    origem = dados["origem"]
    if not re.fullmatch(r"[\w.-]{1,64}", origem) or not isinstance(dados["registros"], list):
        raise ValueError("origem ou lista inválida")
    limpos = []
    for r in dados["registros"][:1000]:
        if not (isinstance(r["folhas"], int) and 0 <= r["folhas"] <= 100_000
                and r["situacao"] in banco.SITUACOES
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["data"])
                and re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", r["hora"])):
            raise ValueError(f"registro inválido: {r.get('chave')!r}")
        limpos.append({k: str(r[k])[:100] if isinstance(r[k], str) else r[k]
                       for k in ("chave", "usuario", "impressora", "folhas", "situacao", "data", "hora")})
    return origem, limpos


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

    def do_POST(self):
        """Servidor central: recebe os registros enviados pelos PCs com cotas."""
        if urlparse(self.path).path != "/registros":
            return self.enviar(404, "text/plain", "Não encontrado.")
        if not _chave_ok(self.client_address[0], self.headers.get("Authorization", "")):
            return self.enviar(401, "text/plain", "Chave inválida.")
        try:
            tamanho = int(self.headers.get("Content-Length", 0))
            if not 0 < tamanho <= 5_000_000:
                raise ValueError("tamanho inválido")
            dados = json.loads(self.rfile.read(tamanho))
            origem, registros = _validar(dados)
        except (ValueError, KeyError, TypeError) as e:
            return self.enviar(400, "text/plain", f"Dados inválidos: {e}")
        banco.gravar_remotos(origem, registros)
        if isinstance(dados.get("limites"), dict):
            banco.gravar_estado("limites_remotos", json.dumps(dados["limites"]))
        self.enviar(200, "application/json", json.dumps({"recebidos": len(registros)}))

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
    return srv
