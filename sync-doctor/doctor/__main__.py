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
    if not cfg.bazarr_url or not cfg.bazarr_api_key:
        sys.exit("Faltan BAZARR_URL y/o BAZARR_API_KEY")
    os.makedirs(cfg.data_dir, exist_ok=True)
    store = Store(os.path.join(cfg.data_dir, "doctor.db"))
    # Lo guardado desde la web manda sobre las variables de entorno.
    saved = store.load_settings()
    if saved:
        try:
            cfg.apply(saved)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("doctor").warning("Ajustes guardados no válidos, se ignoran: %s", e)
    logging.getLogger("doctor").info(
        "Sync Doctor arrancado. Revisa: %s. Horario automático: %s. Acciones automáticas: %s",
        ", ".join("películas" if k == "movies" else "series" for k in cfg.kinds),
        f"{cfg.active_hours} h" if cfg.active_hours.strip() else "siempre",
        ", ".join(f"{v}→{a}" for v, a in cfg.auto_actions.items()) or "ninguna")
    worker = Worker(cfg, store)
    threading.Thread(target=serve, args=(cfg, store, worker), daemon=True).start()
    worker.run_forever()


if __name__ == "__main__":
    main()
