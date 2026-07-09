# jukeplayer/lib/dummy_rotary.py
class DummyRotaryIRQ:
    def __init__(self):
        self._value = 0
    def value(self):
        return self._value
    def set(self, value=0, **kwargs):
        self._value = value
    def add_listener(self, callback):
        pass
