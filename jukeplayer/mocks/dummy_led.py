# jukeplayer/mocks/dummy_led.py — headless LED mock (models the full v3 API:
# the solid/dim/blink primitives AND the signaling vocabulary — every method
# a no-op, so leds.enabled: false exercises all code paths safely).
class DummyLEDController:
    def __init__(self, pin_number: int = 0, invert: bool = False, brightness: int = 100): pass
    # primitives
    def blink(self, interval_ms: int = 500): pass
    def turn_on(self, percent: int = 100): pass
    def turn_off(self): pass
    def set_brightness(self, percent: int = 100): pass
    # vocabulary
    def confirm_blinks(self, n: int = 1): pass
    def long_blink(self): pass
    def start_failure_pulse(self): pass
    def clear_failure(self): pass
