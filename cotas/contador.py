import math
import re
import shutil
import subprocess


def folhas(paginas, copias=1, por_folha=1, frente_verso=False):
    """Ex.: 5 páginas, 2 por folha, frente e verso, 3 cópias = 3 lados -> 2 folhas x 3 = 6."""
    if not paginas:
        return 0
    lados = math.ceil(paginas / max(1, por_folha))
    return (math.ceil(lados / 2) if frente_verso else lados) * max(1, copias)


def paginas_no_intervalo(total, intervalos):
    """Aplica intervalos como "1-3,7" ao total de páginas."""
    if not intervalos or not total:
        return total
    escolhidas = set()
    for parte in intervalos.split(","):
        inicio, traco, fim = parte.strip().partition("-")
        try:
            a = int(inicio or 1)
            b = int(fim) if fim else (total if traco else a)
        except ValueError:
            return total
        escolhidas.update(range(max(1, a), min(total, b) + 1))
    return len(escolhidas)


def contar_paginas_arquivo(caminho):
    """Número de páginas do documento, ou None se não souber contar."""
    try:
        with open(caminho, "rb") as f:
            dados = f.read()
    except OSError:
        return None

    if b"%PDF" in dados[:16]:
        return _pdf(caminho, dados)
    if dados.startswith((b"%!", b"\x04%!")):
        return _postscript(caminho, dados)
    if dados.startswith((b"\xff\xd8", b"\x89PNG", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")):
        return 1
    if _e_texto(dados[:4096]):
        paginas = [p for p in dados.split(b"\f") if p.strip()]
        return max(1, sum(math.ceil((p.count(b"\n") + 1) / 60) for p in paginas))
    return None


def _e_texto(amostra):
    try:
        return bool(amostra) and b"\x00" not in amostra and bool(amostra.decode("utf-8"))
    except UnicodeDecodeError:
        return False


def _pdf(caminho, dados):
    if shutil.which("pdfinfo"):
        saida = subprocess.run(["pdfinfo", caminho], capture_output=True, text=True,
                               timeout=30).stdout
        m = re.search(r"^Pages:\s+(\d+)", saida, re.M)
        if m:
            return int(m.group(1))
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", dados)) or None


def _postscript(caminho, dados):
    totais = [int(x) for x in re.findall(rb"^%%Pages:\s*(\d+)", dados, re.M)]
    if totais and max(totais):
        return max(totais)
    marcas = len(re.findall(rb"^%%Page:", dados, re.M))
    if marcas:
        return marcas
    gs = shutil.which("gs") or shutil.which("gswin64c")
    if gs:
        r = subprocess.run([gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=bbox", caminho],
                           capture_output=True, text=True, timeout=120)
        return r.stderr.count("%%BoundingBox") or None
    return None
