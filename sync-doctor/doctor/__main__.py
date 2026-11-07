"""Punto de entrada: python -m doctor"""
import logging
import os
import sys
import threading

from .config import Config
from .logbuf import BUFFER
from .store import Store
from .web import serve
from .worker import Worker


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    BUFFER.setLevel(logging.INFO)
    logging.getLogger("doctor").addHandler(BUFFER)
    cfg = Config()
    logging.getLogger("doctor").info(
        "Sync Doctor arrancado. Revisa: %s. Horario automático: %s",
        ", ".join("películas" if k == "movies" else "series" for k in cfg.kinds),
        f"{cfg.active_hours} h" if cfg.active_hours.strip() else "siempre")
    if not cfg.bazarr_url or not cfg.bazarr_api_key:
        sys.exit("Faltan BAZARR_URL y/o BAZARR_API_KEY")
    os.makedirs(cfg.data_dir, exist_ok=True)
    store = Store(os.path.join(cfg.data_dir, "doctor.db"))
    worker = Worker(cfg, store)
    threading.Thread(target=serve, args=(cfg, store, worker), daemon=True).start()
    worker.run_forever()


if __name__ == "__main__":
    main()
