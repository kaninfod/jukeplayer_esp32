from jukeplayer.core.state_constants import *
from jukeplayer.core.logger import log
class AppState:
    def __init__(self, initial_state=None):
        
        self._state = {
            TITLE: "Idle",
            ARTIST: "",
            ALBUM: "",
            YEAR: "",
            TRACK_NUMBER: 0,
            TRACK: "",
            PLAYLIST_COUNT: 0,
            PLAYER_STATUS: "idle",
            VOLUME: 0,
            REPEAT_STATUS: False,
            NETWORK_STATUS: "WS:ERR",
            CLIENT_ID: "",
            MEMORY_USAGE: 0,
            NFC_WRITE_STATE: False,
            NFC_ENCODING_ALBUM_ID: None,
            LAST_NFC_SCAN: "",
            WS_CONNECTED: False,
            COVER_URL: "",
            MUTED: False
        }
        if initial_state:
            self._state.update(initial_state)
            
        # A list to store all registered subscriber callbacks
        self._subscribers = []

    def subscribe(self, callback):
        """Register a new listener. Returns a function to unsubscribe."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)
            
        # Return a quick lambda to allow easy unsubscribing if needed
        return lambda: self._subscribers.remove(callback)

    def set(self, updates: dict):
        changed = {}
        for key, value in updates.items():
            if key not in self._state:
                log.debug(f"[APPSTATE] ignoring unknown key: {key}")
                continue
            old = self._state.get(key, None)
            if old != value:
                self._state[key] = value
                changed[key] = value
        
        if changed:
            for cb in self._subscribers:
                try:
                    cb(state=changed)
                except Exception as e:
                    import sys
                    import io
                    buf = io.StringIO()
                    sys.print_exception(e, buf)
                    log.error(f"[APPSTATE] error in state subscriber {cb.__name__}: {e}")
                    for tb_line in buf.getvalue().split("\n"):
                        if tb_line:
                            log.error(f"[APPSTATE]   {tb_line}")

    def get(self, key, default=None):
        return self._state.get(key, default)

    @property
    def data(self):
        return self._state