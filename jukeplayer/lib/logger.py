import time
import ntptime

class Logger:
    def __init__(self, debug=True):
        self._debug = debug
        self._time_synced = False
        
    def sync_time(self):
        try:
            ntptime.settime()
            self._time_synced = True
            self.info("NTP time synced successfully (UTC).")
            return True
        except Exception as e:
            self.error(f"NTP sync failed: {e}")
            return False

    def _get_timestamp(self):
        if not self._time_synced:
            return f"{time.ticks_ms()}ms"
            
        t = time.localtime()
        return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

    def _log(self, level, msg):
        print(f"[{self._get_timestamp()}] [{level}] {msg}")

    def info(self, msg):
        self._log("INFO", msg)

    def warn(self, msg):
        self._log("WARN", msg)

    def error(self, msg):
        self._log("ERROR", msg)

    def debug(self, msg):
        if self._debug:
            self._log("DEBUG", msg)

log = Logger()