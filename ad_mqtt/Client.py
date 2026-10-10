# pylint: disable=attribute-defined-outside-init
import errno
import logging
import re
import socket
import insteon_mqtt.network as IN
import alarmdecoder.event as ADE
from alarmdecoder.util import InvalidMessageError

LOG = logging.getLogger(__name__)

# Matches the start of a panel message:
#   [!KPM:][address],numeric_code,[panel_data],"
# The address contains hex characters and dashes, the numeric code and panel
# data are hex, and the alphanumeric text field always opens with a quote.
# This is used to detect two frames that arrived glued together with no CR/LF
# separating them.
_FRAME_START = re.compile(
    rb'(?:!KPM:)?\[[0-9A-Fa-f\-]+\],[0-9A-Fa-f]+,\[[0-9A-Fa-f]+\],(?=")'
)


class Client (IN.Link):
    # Alarmdecoder signals to implement the device api.  Also need write().
    on_open = ADE.event.Event("Connected event.  f(link)")
    on_close = ADE.event.Event("Close event.  f(link)")
    on_read = ADE.event.Event("Read cb.  f(link, bytes)")
    on_write = ADE.event.Event("Written db.  f(link, data)")

    def __init__(self, host='', port=10000, reconnect_dt=30):
        IN.Link.__init__(self)
        self.addr = (host, port)
        self.reconnect_dt = reconnect_dt
        self._init_vars()

    #-----------------------------------------------------------------------
    def write(self, data):
        if not len(data):
            return

        if not isinstance(data, bytes):
            data = data.encode("utf-8")

        LOG.debug("Adding %s bytes to write buffer of %s bytes: '%s'",
                  len(data), len(self._write_buf), data)
        self._write_buf += data

        # Only need to emit if there was no data in the buffer already.
        self.signal_needs_write.emit(self, True)

    #-----------------------------------------------------------------------
    def retry_connect_dt(self):
        """Return a positive integer (seconds) if the link should reconnect.

        If this returns None, the link will not be reconnected if it closes.
        Otherwise this is the retry interval in seconds to try and reconnect
        the link by calling connect().
        """
        return self.reconnect_dt

    #-----------------------------------------------------------------------
    def connect(self):
        """Connect the link to the device.

        This should connect to the socket, serial port, file, etc.

        Returns:
          bool:  Returns True if the connection was successful or False it
          it failed.
        """
        LOG.info("Connecting to %s:%s", *self.addr)
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect(self.addr)
        except:
            LOG.exception("Failed to connect")
            if self.socket:
                self.socket.close()

            self.socket = None
            return False

        LOG.info("Connected")
        self.signal_connected.emit(self, True)
        self.on_open()
        return True

    #-----------------------------------------------------------------------
    def fileno(self):
        """Return the file descriptor to watch for this link.

        Returns:
          int:  Returns the descriptor (obj.fileno() usually) to monitor.
        """
        return self.socket.fileno()

    #-----------------------------------------------------------------------
    def read_from_link(self):
        """Read data from the link.

        This will be called by the manager when there is data available on
        the file descriptor for reading.

        Returns:
           int:  Return -1 if the link had an error.  Or any other integer
           to indicate success.
        """
        LOG.debug("Reading from alarmdecoder")
        try:
            buf = self.socket.recv(4096)
        except socket.error as e:
            if e.errno in [errno.EWOULDBLOCK, errno.ETIMEDOUT]:
                return 0

            LOG.exception("Error during read")
            if e.errno in [errno.ECONNRESET, errno.EHOSTUNREACH]:
                return -1
            raise

        # If no data was read, the connection was closed.
        if len(buf) == 0:
            return -1

        LOG.debug("Adding %s bytes to read buffer of %d bytes",
                  len(buf), len(self._read_buf))
        self._read_buf += buf
        self.parse_read_buf()
        return 0

    #-----------------------------------------------------------------------
    def parse_read_buf(self):
        """Process any complete messages found in the read buffer.

        AlarmDecoder messages may be terminated by CR, LF or CRLF and several
        messages can arrive in a single read.  Split on any of those
        delimiters so that each message is handled individually instead of
        being concatenated into one bogus panel message.
        """
        while True:
            # Find the first line terminator (either CR or LF).
            nl = self._read_buf.find(b"\n")
            cr = self._read_buf.find(b"\r")
            if nl == -1:
                idx = cr
            elif cr == -1:
                idx = nl
            else:
                idx = min(nl, cr)

            # No line terminator yet - wait for more data.
            if idx == -1:
                break

            line = self._read_buf[:idx]

            # Consume the terminator and any repeated CR/LF characters.
            end = idx
            while self._read_buf[end:end + 1] in (b"\r", b"\n"):
                end += 1
            self._read_buf = self._read_buf[end:]

            # Ignore empty lines (e.g. a stray CRLF).
            if not line:
                continue

            for part in self._split_glued_frames(line):
                self._process_line(part)

    #-----------------------------------------------------------------------
    def _split_glued_frames(self, line):
        """Split a line into multiple frames when they are glued together.

        Usually each panel frame is terminated by CR/LF and the splitting in
        parse_read_buf handles them.  When the panel emits several frames
        back-to-back the earlier frame can omit the closing quote on its
        alphanumeric text field, and the following frame then starts
        immediately with no CR/LF in between.  Detect every frame header and
        split the blob so each frame is parsed on its own.
        """
        starts = [match.start() for match in _FRAME_START.finditer(line)]

        # A single frame (or no panel frame at all) needs no splitting.
        if len(starts) <= 1:
            return [line]

        parts = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(line)
            parts.append(line[start:end])
        return parts

    #-----------------------------------------------------------------------
    def _process_line(self, line):
        """Dispatch a single message, isolating any handling errors.

        A malformed or unexpected message must not be allowed to propagate
        up through the event callbacks and out of the poll loop, which would
        crash the whole bridge (and trigger a container restart).
        """
        LOG.debug("Processing line '%s'", line)
        try:
            self.on_read(data=line)
            return
        except InvalidMessageError:
            pass
        except Exception:  # pylint: disable=broad-except
            LOG.exception("Error processing line '%s'", line)
            return

        # When the panel emits several frames back-to-back the preceding
        # frame can arrive without the closing quote on its alphanumeric
        # text field.  Try to repair it rather than silently dropping the
        # message.
        if line and not line.endswith(b'"'):
            repaired = line + b'"'
            try:
                LOG.debug("Retrying repaired line '%s'", repaired)
                self.on_read(data=repaired)
                return
            except Exception:  # pylint: disable=broad-except
                pass

        LOG.error("Discarding invalid message '%s'", line)

    #-----------------------------------------------------------------------
    def write_to_link(self, t):
        """Write data from the link.

        This will be called by the manager when the file descriptor can be
        written to.  It will only be called after the link as emitted the
        signal_needs_write(True).  Once all the data has been written, the
        link should call self.signal_needs_write.emit(False).

        Args:
           t (float):  The current time (time.time).
        """
        num = 0
        LOG.debug("Writing to device")
        try:
            if self._write_buf:
                num = self.socket.send(self._write_buf)
        except socket.error as e:
            # If we can't actually write, then return and try again later.
            if e.errno in [errno.EWOULDBLOCK, errno.ETIMEDOUT]:
                return

            # Connection is closed.
            elif e.errno == errno.ECONNRESET:
                self.close()
                return

            raise

        LOG.debug("Wrote %d bytes", num)
        if num:
            self.on_write(data=self._write_buf[:num])
            self._write_buf = self._write_buf[num:]

        if not len(self._write_buf):
            self.signal_needs_write.emit(self, False)

    #-----------------------------------------------------------------------
    def close(self):
        """Close the link.

        The link must call self.signal_closing.emit() after closing.
        """
        if self.isClosing or self.socket is None:
            return

        LOG.info("Closing device")
        self.isClosing = True
        self.signal_closing.emit(self)
        self.on_close()

        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass

        self.socket.close()
        self._init_vars()

    #-----------------------------------------------------------------------
    def _init_vars(self):
        "TODO:"
        self.socket = None
        self._fileno = None
        self._read_buf = bytes()
        self._write_buf = bytes()
        self.isClosing = False

    #-----------------------------------------------------------------------
