"""Comandos do administrador (rodar como root ou Administrador).

  cotas-admin listar
  cotas-admin padrao 15             limite diário de quem não tem limite próprio
  cotas-admin limite maria 30       limite próprio (ou "padrao" para tirar)
  cotas-admin extra maria 5         +5 folhas só hoje
  cotas-admin devolver maria        devolve as folhas usadas hoje
  cotas-admin isentar professor     sem limite (desisentar para voltar)
  cotas-admin historico maria --dias 30
  cotas-admin relatorio 2026-10     consumo do mês (--planilha arquivo.xlsx para o Excel)
  cotas-admin senha                 troca a senha do painel
  cotas-admin rede ligar|desligar   relatórios pela rede (pede uma senha só para eles)
  cotas-admin servidor IP CHAVE     envia os registros ao servidor central
  cotas-admin servidor desligar     para de enviar
  cotas-admin ativar | desativar    liga/desliga o controle
  cotas-admin diagnostico           verifica serviço, impressoras e fila
"""
import argparse
import datetime as dt
import getpass
import re
import socket
import sqlite3
import subprocess
import sys

from . import banco, config, diagnostico, envio, relatorio


def tabela(cabecalho, linhas):
    larguras = [max(len(str(x)) for x in col) for col in zip(cabecalho, *linhas)]
    for linha in [cabecalho, ["-" * w for w in larguras], *linhas]:
        print("  ".join(str(x).ljust(w) for x, w in zip(linha, larguras)))


def listar(a, cfg):
    ativo = banco.estado()["controle_ativo"]
    print(f"Controle: {'ATIVO' if ativo else 'DESATIVADO'} · "
          f"Limite padrão: {cfg['limite_diario_padrao']} folha(s)/dia\n")
    linhas = []
    for u in banco.usuarios_conhecidos():
        s = banco.saldo(u, cfg)
        limite = "isento" if s["isento"] else s["limite"]
        linhas.append((u, limite, s["usados"], s["extra"], s["restantes"]))
    tabela(("usuário", "limite", "usadas hoje", "extra", "restantes"), linhas)


def padrao(a, cfg):
    cfg["limite_diario_padrao"] = a.folhas
    config.salvar(cfg)
    print(f"Limite padrão: {a.folhas} folha(s) por dia.")


def limite(a, cfg):
    if a.folhas == "padrao":
        banco.remover_limite(a.usuario)
        print(f"{a.usuario} voltou ao limite padrão.")
    else:
        banco.definir_limite(a.usuario, int(a.folhas))
        print(f"{a.usuario}: {a.folhas} folha(s) por dia.")


def extra(a, cfg):
    banco.adicionar_extra(a.usuario, a.folhas)
    print(f"{a.usuario} tem agora {banco.saldo(a.usuario, cfg)['restantes']} folha(s).")


def devolver(a, cfg):
    banco.estornar_hoje(a.usuario)
    print(f"{a.usuario} tem agora {banco.saldo(a.usuario, cfg)['restantes']} folha(s).")


def isentar(a, cfg, isento=True):
    alvo = banco.normalizar(a.usuario)
    lista = [u for u in cfg["usuarios_isentos"] if banco.normalizar(u) != alvo]
    cfg["usuarios_isentos"] = lista + [alvo] if isento else lista
    config.salvar(cfg)
    print(f"{a.usuario} {'imprime sem limite' if isento else 'voltou a ter limite'}.")


def historico(a, cfg):
    tabela(("data", "hora", "usuário", "folhas", "situação", "documento"),
           [(h["data"], h["hora"][:5], h["usuario"], h["folhas"], h["situacao"],
             (h["titulo"] or "")[:40]) for h in banco.historico(a.usuario, a.dias)])


def mensal(a, cfg):
    mes = a.mes or dt.date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        sys.exit("Mês no formato AAAA-MM, ex.: 2026-10")
    if a.planilha:
        with open(a.planilha, "wb") as f:
            f.write(relatorio.planilha_mes(mes))
        print(f"Planilha salva em {a.planilha}")
        return
    r = banco.relatorio(mes)
    tabela(("usuário", "folhas", "impressões", "bloqueadas"),
           [(u["usuario"], u["folhas"], u["impressoes"], u["bloqueios"]) for u in r["usuarios"]])
    print(f"\nTotal: {sum(u['folhas'] for u in r['usuarios'])} folha(s) em {len(r['dias'])} dia(s).")


def senha(a, cfg):
    nova = getpass.getpass("Nova senha do painel: ")
    if len(nova) < 4 or getpass.getpass("Repita a senha: ") != nova:
        sys.exit("Senha inválida (mínimo 4 caracteres) ou as duas não conferem.")
    banco.definir_senha(nova, substituir=True)
    print("Senha trocada.")


def rede(a, cfg):
    ligar = a.acao == "ligar"
    if ligar:
        nova = getpass.getpass("Senha para ver os relatórios (diferente da do painel): ")
        if len(nova) < 4 or getpass.getpass("Repita a senha: ") != nova:
            sys.exit("Senha inválida (mínimo 4 caracteres) ou as duas não conferem.")
        if banco.conferir_senha(nova):
            sys.exit("Use uma senha diferente da do administrador do painel.")
        banco.definir_senha(nova, substituir=True, chave="senha_relatorio")
    cfg["relatorio_rede"] = ligar
    config.salvar(cfg)
    subprocess.run(["systemctl", "restart", "cotas-impressao.service"], check=False)
    if not ligar:
        print("Relatórios pela rede desligados.")
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # não envia nada: só descobre o IP da rede
        ip = s.getsockname()[0]
    except OSError:
        ip = "IP-DESTE-PC"
    print(f"Relatórios na rede: http://{ip}:{cfg['porta_relatorio']}/relatorio")


def servidor(a, cfg):
    if a.endereco == "desligar":
        config.ARQ_SERVIDOR.unlink(missing_ok=True)
        return print("Envio ao servidor desligado.")
    if a.endereco is None:
        atual = envio.carregar()
        pendentes = len(banco.pendentes(int(banco.ler_estado("enviado_ate", -1)), limite=10**9))
        return print(f"Servidor: {atual['url'] if atual else 'nenhum'} · {pendentes} registro(s) "
                     "aguardando envio")
    if not a.chave:
        sys.exit("Informe a chave mostrada na instalação do servidor.")
    url = a.endereco if "://" in a.endereco else f"http://{a.endereco}:{cfg['porta_relatorio']}"
    novo = {"url": url, "chave": a.chave}
    banco.gravar_estado("enviado_ate", -1)  # servidor novo recebe o histórico inteiro
    try:
        n = envio.enviar_pendentes(novo, forcar=True)
    except Exception as e:
        sys.exit(f"Não consegui enviar para {url}: {e}\nConfira o IP, a chave e se o servidor está ligado.")
    envio.salvar(url, a.chave)
    print(f"Servidor configurado: {url} ({n} registro(s) enviados agora). "
          "Daqui em diante o envio é automático, a cada minuto.")


def controle(ativo):
    def comando(a, cfg):
        banco.definir_controle(ativo)
        print("Controle ATIVADO." if ativo else "Controle DESATIVADO: impressões sem desconto.")
    return comando


def verificar(a, cfg):
    simbolos = {"ok": "✓", "aviso": "!", "erro": "✗"}
    itens = diagnostico.relatorio()
    for situacao, texto in itens:
        print(f" {simbolos[situacao]}  {texto}")
    if any(s == "erro" for s, _ in itens):
        sys.exit(1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cotas-admin", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)

    def comando(nome, funcao, *args):
        s = sub.add_parser(nome)
        for arg in args:
            s.add_argument(arg, type=int if arg == "folhas" and nome != "limite" else str)
        s.set_defaults(f=funcao)
        return s

    comando("listar", listar)
    comando("padrao", padrao, "folhas")
    comando("limite", limite, "usuario", "folhas")
    comando("extra", extra, "usuario", "folhas")
    comando("devolver", devolver, "usuario")
    comando("isentar", isentar, "usuario")
    comando("desisentar", lambda a, c: isentar(a, c, False), "usuario")
    comando("historico", historico).add_argument("usuario", nargs="?")
    sub.choices["historico"].add_argument("--dias", type=int, default=7)
    comando("relatorio", mensal).add_argument("mes", nargs="?")
    sub.choices["relatorio"].add_argument("--planilha")
    comando("senha", senha)
    comando("rede", rede).add_argument("acao", choices=["ligar", "desligar"])
    s = comando("servidor", servidor)
    s.add_argument("endereco", nargs="?")
    s.add_argument("chave", nargs="?")
    comando("ativar", controle(True))
    comando("desativar", controle(False))
    comando("diagnostico", verificar)

    a = p.parse_args(argv)
    try:
        banco.inicializar()
        a.f(a, config.carregar())
    except (OSError, sqlite3.OperationalError) as e:
        sys.exit(f"Sem permissão ({e}). Rode como root (sudo) ou como Administrador.")
