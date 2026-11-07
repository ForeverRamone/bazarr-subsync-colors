"""Últimas líneas del registro, en memoria, para mostrarlas en la web."""
import logging
import threading
from collections import deque


class RingHandler(logging.Handler):
    def __init__(self, size=500):
        super().__init__()
        self.lines = deque(maxlen=size)
        self.seq = 0
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%d/%m %H:%M:%S"))

    def emit(self, record):
        try:
            text = self.format(record)
            if record.exc_info:  # sin el traceback entero, solo la última línea
                text = text.splitlines()[0] + " — " + text.splitlines()[-1]
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self.seq += 1
            self.lines.append({"n": self.seq, "level": record.levelname, "text": text})

    def since(self, after=0):
        with self._lock:
            return [line for line in self.lines if line["n"] > after]


BUFFER = RingHandler()
