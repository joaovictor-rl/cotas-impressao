"""Backend "cotas" do CUPS: todo trabalho passa por aqui antes de ir para a impressora.

A impressora fica com o endereço "cotas:/<endereço original>". Se o usuário tem
saldo, o trabalho segue para o backend original (usb, ipp, socket...); se não,
o CUPS cancela o trabalho.
"""
import glob
import os
import pwd
import shlex
import sys

PREFIXO = "cotas:/"
OK, FALHOU, CANCELAR = 0, 1, 5  # códigos de saída de backend do CUPS


def avisar(nivel, texto):
    print(f"{nivel}: {texto}", file=sys.stderr, flush=True)


def ler_opcoes(texto):
    try:
        partes = shlex.split(texto)
    except ValueError:
        partes = texto.split()
    return dict(p.partition("=")[::2] for p in partes)


def calcular_folhas(jid, copias, opcoes, arquivo=None):
    from . import contador

    pasta = os.environ.get("CUPS_REQUESTROOT", "/var/spool/cups")
    arquivos = sorted(glob.glob(os.path.join(pasta, f"d{int(jid):05d}-*"))) or [arquivo]
    paginas = 0
    for arq in arquivos:
        n = contador.contar_paginas_arquivo(arq) if arq else None
        if n is None:
            return None
        paginas += n
    paginas = contador.paginas_no_intervalo(paginas, opcoes.get("page-ranges"))
    frente_verso = (opcoes.get("sides", "").startswith("two-sided")
                    or opcoes.get("Duplex") in ("DuplexNoTumble", "DuplexTumble"))
    n_up = opcoes.get("number-up", "1")
    por_folha = int(n_up) if n_up.isdigit() else 1
    return contador.folhas(paginas, copias, por_folha, frente_verso)


def executar_original(uri, argv):
    programa = os.path.join(os.environ.get("CUPS_SERVERBIN", "/usr/lib/cups"),
                            "backend", uri.split(":", 1)[0])
    if not os.path.isfile(programa):
        avisar("ERROR", f"Backend original não encontrado: {programa}")
        return FALHOU
    # Regra do CUPS: o backend só roda sem root ("lp") se todos podem lê-lo E executá-lo.
    # O "usb" (0744) precisa de root para acessar a porta USB.
    if os.geteuid() == 0 and os.stat(programa).st_mode & 0o005 == 0o005:
        lp = pwd.getpwnam("lp")
        os.setgroups([])
        os.setgid(lp.pw_gid)
        os.setuid(lp.pw_uid)
    os.execve(programa, [uri] + argv[1:], dict(os.environ, DEVICE_URI=uri))


def main(argv):
    if len(argv) == 1:
        return OK  # o CUPS pergunta quais impressoras o backend oferece: nenhuma
    if len(argv) not in (6, 7):
        avisar("ERROR", "uso: cotas job-id usuario titulo copias opcoes [arquivo]")
        return FALHOU

    jid, usuario, titulo, copias, opcoes = argv[1:6]
    uri = os.environ.get("DEVICE_URI", "")
    if not uri.startswith(PREFIXO):
        avisar("ERROR", f"Endereço inválido: {uri!r}")
        return FALHOU
    original = uri[len(PREFIXO):]

    try:
        from . import banco, config
        cfg = config.carregar()
        impressora = os.environ.get("PRINTER", "")
        if impressora not in cfg["impressoras_ignoradas"]:
            folhas = calcular_folhas(jid, int(copias or 1), ler_opcoes(opcoes),
                                     argv[6] if len(argv) == 7 else None)
            liberar, _, motivo = banco.decidir(f"cups-{jid}", usuario, impressora,
                                               titulo, folhas, cfg)
            if not liberar:
                avisar("INFO", f"Cancelado pela cota de impressão: {motivo}")
                return CANCELAR
    except Exception as e:
        # Se o controle falhar, a impressão continua (sem desconto)
        avisar("ERROR", f"Falha no controle de cotas ({e!r}); imprimindo sem descontar.")

    return executar_original(original, argv)
