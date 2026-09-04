import time

try:
    import ntptime
except ImportError:
    ntptime = None

try:
    import socket
except ImportError:
    try:
        import usocket as socket
    except ImportError:
        socket = None

_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
}

_SYSLOG_SEVERITY = {
    "DEBUG": 7,
    "INFO": 6,
    "WARN": 4,
    "WARNING": 4,
    "ERROR": 3,
}

_SYSLOG_FACILITY = {
    "kern": 0,
    "user": 1,
    "mail": 2,
    "daemon": 3,
    "auth": 4,
    "syslog": 5,
    "lpr": 6,
    "news": 7,
    "uucp": 8,
    "cron": 9,
    "authpriv": 10,
    "ftp": 11,
    "ntp": 12,
    "logaudit": 13,
    "logalert": 14,
    "clock": 15,
    "local0": 16,
    "local1": 17,
    "local2": 18,
    "local3": 19,
    "local4": 20,
    "local5": 21,
    "local6": 22,
    "local7": 23,
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _normalise_level(level):
    if level is None:
        return "INFO"
    return str(level).upper()


def _format_console(ts, level, msg):
    return f"[{ts}] [{level}] {msg}"


class ConsoleHandler:
    """Logs to the MicroPython REPL / serial console via print()."""

    def emit(self, ts, level, msg):
        print(_format_console(ts, level, msg))


class SyslogHandler:
    """
    Buffers log lines and sends them as RFC 3164 UDP syslog packets.

    The handler starts in offline mode and accumulates lines in a queue.
    Once mark_online() is called the buffered lines are flushed to the
    configured syslog server.  Flushing is intentionally asynchronous and
    must be driven by calling flush() from an async loop.
    """

    def __init__(self, host, port=514, facility="local0", hostname="jukeplayer",
                 tag="jukeplayer", boot_buffer_lines=50, max_queue_lines=200,
                 flush_batch_size=50):
        self.host = host
        self.port = int(port)
        self.facility = self._facility_code(facility)
        self.hostname = str(hostname)
        self.tag = str(tag)
        self.boot_buffer_lines = max(0, int(boot_buffer_lines))
        self.max_queue_lines = max(self.boot_buffer_lines, int(max_queue_lines))
        self.flush_batch_size = max(1, int(flush_batch_size))

        self._online = False
        self._queue = []
        self._sock = None
        self._failed_count = 0
        self._max_consecutive_failures = 10

    def _facility_code(self, facility):
        if isinstance(facility, int):
            return max(0, min(23, facility))
        return _SYSLOG_FACILITY.get(str(facility).lower(), 16)

    def _format_rfc3164(self, ts, level, msg):
        pri = self.facility * 8 + _SYSLOG_SEVERITY.get(level, 6)
        return f"<{pri}>{ts} {self.hostname} {self.tag}: [{level}] {msg}"

    def emit(self, ts, level, msg):
        line = self._format_rfc3164(ts, level, msg)
        self._queue.append(line)
        cap = self.max_queue_lines if self._online else self.boot_buffer_lines
        while len(self._queue) > cap:
            self._queue.pop(0)

    def mark_online(self):
        """Start flushing queued lines to the syslog server."""
        self._online = True

    def _ensure_socket(self):
        if self._sock is not None:
            return True
        if socket is None:
            return False
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
            return True
        except Exception:
            self._sock = None
            return False

    async def flush(self):
        """Send queued syslog lines. Should be called from an async loop."""
        if not self._online or not self._queue:
            return

        if not self._ensure_socket():
            return

        # Back off for one cycle after many consecutive failures so a
        # transient network/server problem does not spam socket errors.
        if self._failed_count >= self._max_consecutive_failures:
            self._failed_count = 0
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            return

        sent = 0
        while self._queue and sent < self.flush_batch_size:
            line = self._queue.pop(0)
            try:
                self._sock.sendto(line.encode("utf-8"), (self.host, self.port))
                self._failed_count = 0
                sent += 1
            except Exception:
                # Put the line back and stop trying this cycle.
                self._queue.insert(0, line)
                self._failed_count += 1
                break


class Logger:
    """Configurable logger supporting console and syslog handlers."""

    def __init__(self, level="INFO"):
        self._level = _normalise_level(level)
        self._handlers = []
        self._time_synced = False

    def add_handler(self, handler):
        self._handlers.append(handler)

    def set_level(self, level):
        self._level = _normalise_level(level)

    def sync_time(self):
        if ntptime is None:
            self.warn("ntptime not available; time will remain unsynced")
            return False
        try:
            ntptime.settime()
            self._time_synced = True
            self.info("NTP time synced successfully (UTC).")
            return True
        except Exception as e:
            self.error(f"NTP sync failed: {e}")
            return False

    def _console_timestamp(self):
        if not self._time_synced:
            if hasattr(time, "ticks_ms"):
                return f"{time.ticks_ms()}ms"
            return "0ms"
        t = time.localtime()
        return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

    def _syslog_timestamp(self):
        if not self._time_synced:
            if hasattr(time, "ticks_ms"):
                return f"{time.ticks_ms()}ms"
            return "0ms"
        t = time.localtime()
        return f"{_MONTHS[t[1] - 1]} {t[2]:2d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

    def _should_log(self, level):
        return _LEVELS.get(level, 20) >= _LEVELS.get(self._level, 20)

    def _log(self, level, msg):
        if not self._should_log(level):
            return

        ts_console = self._console_timestamp()
        ts_syslog = self._syslog_timestamp()

        for handler in self._handlers:
            if isinstance(handler, SyslogHandler):
                handler.emit(ts_syslog, level, msg)
            else:
                handler.emit(ts_console, level, msg)

    def debug(self, msg):
        self._log("DEBUG", msg)

    def info(self, msg):
        self._log("INFO", msg)

    def warn(self, msg):
        self._log("WARN", msg)

    def error(self, msg):
        self._log("ERROR", msg)

    def configure_from_config(self, config):
        """
        Configure handlers from config["logging"].

        Safe to call multiple times: existing handlers are preserved if
        their configuration has not changed, so buffered syslog boot logs
        are not lost when main.py reconfigures the same logger.
        """
        if not config:
            return

        logging_cfg = config.get("logging", {})
        self.set_level(logging_cfg.get("level", "INFO"))

        # Console handler
        console_cfg = logging_cfg.get("console", {})
        want_console = console_cfg.get("enabled", True)
        existing_console = next(
            (h for h in self._handlers if isinstance(h, ConsoleHandler)), None
        )
        if want_console and existing_console is None:
            self.add_handler(ConsoleHandler())
        elif not want_console and existing_console is not None:
            self._handlers.remove(existing_console)

        # Syslog handler
        syslog_cfg = logging_cfg.get("syslog", {})
        want_syslog = syslog_cfg.get("enabled", False) and socket is not None
        existing_syslog = next(
            (h for h in self._handlers if isinstance(h, SyslogHandler)), None
        )

        new_params = {
            "host": syslog_cfg.get("host", "127.0.0.1"),
            "port": syslog_cfg.get("port", 514),
            "facility": syslog_cfg.get("facility", "local0"),
            "hostname": syslog_cfg.get("hostname", "jukeplayer"),
            "tag": syslog_cfg.get("tag", "jukeplayer"),
            "boot_buffer_lines": syslog_cfg.get("boot_buffer_lines", 50),
        }

        syslog_changed = (
            existing_syslog is None
            or existing_syslog.host != new_params["host"]
            or existing_syslog.port != new_params["port"]
            or existing_syslog.facility != existing_syslog._facility_code(new_params["facility"])
            or existing_syslog.hostname != new_params["hostname"]
            or existing_syslog.tag != new_params["tag"]
        )

        if not want_syslog and existing_syslog is not None:
            self._handlers.remove(existing_syslog)
        elif want_syslog and syslog_changed:
            if existing_syslog is not None:
                self._handlers.remove(existing_syslog)
            self.add_handler(SyslogHandler(**new_params))

    def mark_syslog_online(self):
        """Tell all syslog handlers that the network is up."""
        for handler in self._handlers:
            if isinstance(handler, SyslogHandler):
                handler.mark_online()

    async def flush_syslog(self):
        """Flush any queued syslog lines. Call from an async loop."""
        for handler in self._handlers:
            if isinstance(handler, SyslogHandler):
                await handler.flush()


# Default global logger: console enabled so boot errors are always visible
# before config.json is loaded.  configure_from_config() can adjust later.
log = Logger(level="INFO")
log.add_handler(ConsoleHandler())
