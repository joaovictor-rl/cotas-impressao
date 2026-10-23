"""Devolve cada impressora ao endereço que ela tinha antes do programa.

Uso (como root):  python3 -m cotas.restaurar
"""
import json
import subprocess

import cups

from . import config
from .backend_cups import PREFIXO

ARQ_ORIGINAIS = config.PASTA_DADOS / "enderecos_originais.json"


def carregar_originais():
    try:
        with open(ARQ_ORIGINAIS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def salvar_original(nome, uri):
    originais = carregar_originais()
    originais[nome] = uri
    config.PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    with open(ARQ_ORIGINAIS, "w", encoding="utf-8") as f:
        json.dump(originais, f, indent=2, ensure_ascii=False)


def restaurar():
    originais = carregar_originais()
    restauradas = []
    for nome, info in cups.Connection().getPrinters().items():
        uri = info.get("device-uri", "")
        if not uri.startswith(PREFIXO):
            continue
        original = originais.get(nome, "")
        # Se o endereço salvo não servir, o original é o que vem depois de "cotas:/"
        if not original or original.startswith(PREFIXO):
            original = uri[len(PREFIXO):]
        subprocess.run(["lpadmin", "-p", nome, "-v", original], check=True)
        subprocess.run(["cupsenable", nome], check=False)
        restauradas.append((nome, original))
    return restauradas


def main():
    restauradas = restaurar()
    for nome, uri in restauradas:
        print(f"Impressora {nome} -> {uri}")
    if not restauradas:
        print("Nenhuma impressora estava passando pelo controle de cotas.")


if __name__ == "__main__":
    main()
