# jukeplayer/lib/dummy_rotary.py
class DummyLEDController:
    def __init__(self, pin_number: int, invert: bool = False): pass
    def blink(self, interval_ms: int = 500): pass
    def turn_on(self): pass
    def turn_off(self): pass

