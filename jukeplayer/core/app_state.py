from jukeplayer.core.state_constants import *
class AppState:
    def __init__(self, initial_state=None):
        
        self._state = {
            TITLE: "Idle",
            ARTIST: "",
            ALBUM: "",
            TRACK: "",
            PLAYER_STATUS: "idle",
            VOLUME: 0,
            REPEAT_STATUS: False,
            NETWORK_STATUS: "WS:ERR",
            MUTE_STATUS: False,
            CLIENT_ID: "",
            MEMORY_USAGE: 0,
            NFC_WRITE_STATE: False,
            NFC_ENCODING_ALBUM_ID: None,
            LAST_NFC_SCAN: "",
            WS_CONNECTED: False,
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
                print(f"[AppState] Ignoring unknown key: {key}")
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
                    print(f"Error in state subscriber {cb.__name__}: {e}")

    def get(self, key, default=None):
        return self._state.get(key, default)

    @property
    def data(self):
        return self._state