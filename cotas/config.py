import json
import os
from pathlib import Path

PASTA_CONFIG = Path("/etc/cotas-impressao")
PASTA_DADOS = Path("/var/lib/cotas-impressao")

# Permite rodar testes sem mexer no sistema
PASTA_CONFIG = Path(os.environ.get("COTAS_CONFIG_DIR", PASTA_CONFIG))
PASTA_DADOS = Path(os.environ.get("COTAS_DATA_DIR", PASTA_DADOS))
ARQ_CONFIG = PASTA_CONFIG / "config.json"
ARQ_BANCO = PASTA_DADOS / "cotas.db"
ARQ_LOG = PASTA_DADOS / "servico.log"

PADRAO = {
    "limite_diario_padrao": 10,
    "porta_api": 8631,
    "usuarios_isentos": ["root"],
    "impressoras_ignoradas": ["PDF"],
    "paginas_desconhecidas": "contar_1",  # ou "bloquear" / "liberar"
}


def carregar():
    cfg = dict(PADRAO)
    try:
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def salvar(cfg):
    PASTA_CONFIG.mkdir(parents=True, exist_ok=True)
    tmp = ARQ_CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ARQ_CONFIG)


def garantir_pastas():
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    if not ARQ_CONFIG.exists():
        salvar(PADRAO)
