import json
import logging
import os

LOG = logging.getLogger(__name__)


class RfLog:
    """Append-only JSON-lines log of RF messages, known and unknown serials.

    Used to map and debug RF devices: each line records the time, serial
    number, whether the serial is a known device (plus its loop/entity
    mapping), raw value, battery/supervision flags and the open/closed
    state of each loop.  The file is capped at max_bytes; when a write
    would exceed the cap the current file is rotated to <path>.1
    (replacing any previous backup) and writing continues in a fresh
    file so the active file never grows beyond the cap.
    """

    def __init__(self, path, max_bytes=100 * 1024 * 1024):
        self.path = path
        self.max_bytes = max_bytes

    def write(self, record):
        data = (json.dumps(record) + "\n").encode("utf-8")

        try:
            size = self._size()
            if size > 0 and size + len(data) > self.max_bytes:
                self._rotate()

            with open(self.path, "ab") as f:
                f.write(data)
        except OSError:
            LOG.exception("Error writing RF log %s", self.path)

    def _size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def _rotate(self):
        LOG.info("RF log %s reached size cap; rotating to %s.1",
                 self.path, self.path)
        os.replace(self.path, self.path + ".1")
