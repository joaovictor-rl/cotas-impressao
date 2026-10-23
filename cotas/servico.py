import logging
import logging.handlers
import sys

from . import api, banco, config


def configurar_log():
    handlers = [logging.handlers.RotatingFileHandler(
        config.ARQ_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr:  # pythonw.exe não tem console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    config.garantir_pastas()
    configurar_log()
    banco.inicializar()
    try:
        api.iniciar(config.carregar()["porta_api"])
    except OSError:
        logging.info("Serviço já está rodando; esta cópia vai sair.")
        return

    if config.WINDOWS:
        from .monitor_windows import Monitor
    else:
        from .monitor_linux import Monitor
    logging.info("Serviço de cotas iniciado")
    Monitor().executar()
