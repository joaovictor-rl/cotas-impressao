"""Relatório de consumo por mês."""
import csv
import io

from . import banco


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
