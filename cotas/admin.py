"""Comandos do administrador (rodar como root ou Administrador).

  cotas-admin listar
  cotas-admin padrao 15             limite diário de quem não tem limite próprio
  cotas-admin limite maria 30       limite próprio (ou "padrao" para tirar)
  cotas-admin extra maria 5         +5 folhas só hoje
  cotas-admin devolver maria        devolve as folhas usadas hoje
  cotas-admin isentar professor     sem limite (desisentar para voltar)
  cotas-admin historico maria --dias 30
  cotas-admin senha                 troca a senha do painel
  cotas-admin ativar | desativar    liga/desliga o controle
  cotas-admin diagnostico           verifica serviço, impressoras e fila
"""
import argparse
import getpass
import sqlite3
import sys

from . import banco, config, diagnostico


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


def senha(a, cfg):
    nova = getpass.getpass("Nova senha do painel: ")
    if len(nova) < 4 or getpass.getpass("Repita a senha: ") != nova:
        sys.exit("Senha inválida (mínimo 4 caracteres) ou as duas não conferem.")
    banco.definir_senha(nova, substituir=True)
    print("Senha trocada.")


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
    comando("senha", senha)
    comando("ativar", controle(True))
    comando("desativar", controle(False))
    comando("diagnostico", verificar)

    a = p.parse_args(argv)
    try:
        banco.inicializar()
        a.f(a, config.carregar())
    except (OSError, sqlite3.OperationalError) as e:
        sys.exit(f"Sem permissão ({e}). Rode como root (sudo) ou como Administrador.")
