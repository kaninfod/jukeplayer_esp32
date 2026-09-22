# jukeplayer/hardware/led.py — signaling vocabulary (v3, 2026-09-21)
#
# The LED speaks in sequences of fast (".") and long ("-") elements:
#   FAST_MS  = the on time of "."
#   LONG_MS  = the on time of "-"
#   GAP_MS   = the dark gap between the elements (makes . . . readable)
#
# The two-tier model:
#   transient patterns (confirm_blinks/long_blink) — fire once, return to base
#   persistent (start_failure_pulse) — runs until clear_failure()
#
# Every pattern is PWM-scaled by the global brightness (the config's
# leds.brightness): the on-phase duty = the brightness, so the patterns dim
# uniformly. The solid/quiet base is the LED off.
#
# Contexts: the sequence tasks require the running loop (schedule from the
# app's run context — the button/NFC handlers are already async). The PWM
# parts are context-free.
import asyncio
from machine import Pin, PWM

FAST_MS = 70
LONG_MS = 800
GAP_MS = 180


class LEDController:
    def __init__(self, pin_number: int, invert: bool = False, brightness: int = 100):
        self.pin = Pin(pin_number, Pin.OUT)
        self.invert = invert
        self._brightness = max(0, min(100, brightness))
        self._pwm = None
        self._sequence_task = None
        self._pulse_task = None
        self._blink_task = None
        self._set_raw(False)

    # --- low-level ---

    def _set_raw(self, on):
        """Direct drive honoring invert (on=True lights the LED)."""
        self.pin.value(0 if (self.invert == on) else 1)

    def _stop_pwm(self):
        if self._pwm:
            try:
                self._pwm.deinit()
            except Exception:
                pass
            self._pwm = None

    # --- brightness ---

    def _scale(self, duty_percent):
        """Pattern duty scaled by the global brightness."""
        return max(0, min(100, int(duty_percent * self._brightness / 100)))

    def set_brightness(self, percent: int):
        """Runtime override of the global brightness; applies to the next pattern."""
        self._brightness = max(0, min(100, percent))

    # --- the sequence engine ---

    def _stop_patterns(self):
        """The latest pattern wins: cancel anything running."""
        if self._sequence_task:
            self._sequence_task.cancel()
            self._sequence_task = None
        if self._pulse_task:
            self._pulse_task.cancel()
            self._pulse_task = None
        self._stop_pwm()

    def _start_sequence(self, on_times):
        self._stop_patterns()
        self._sequence_task = asyncio.create_task(self._run_sequence(on_times))

    async def _run_sequence(self, on_times):
        """On/gap alternation. The on-phase honors the global brightness: at
        100% a plain high; below that a PWM at the brightness duty."""
        try:
            for on_ms in on_times:
                if self._brightness >= 100:
                    self._set_raw(True)
                else:
                    self._pwm = PWM(self.pin, freq=1000, duty_u16=int(self._brightness * 655.35))
                await asyncio.sleep_ms(on_ms)
                self._stop_pwm()
                self._set_raw(False)
                await asyncio.sleep_ms(GAP_MS)
        except asyncio.CancelledError:
            pass

    # --- the vocabulary ---

    def blink(self, interval_ms: int):
        """Blink with on/off each interval_ms — requires the loop context.
        Kept alongside the vocabulary: the boot pulse uses it."""
        self._stop_patterns()
        if self._blink_task:
            self._blink_task.cancel()
        self._blink_task = asyncio.create_task(self._blink_loop(interval_ms))

    async def _blink_loop(self, interval_ms: int):
        on = True
        try:
            while True:
                self._set_raw(on)
                on = not on
                await asyncio.sleep_ms(interval_ms)
        except asyncio.CancelledError:
            pass

    def confirm_blinks(self, n: int):
        """n fast elements then off — the play ('.'), next ('..'), prev ('...')."""
        self._start_sequence([FAST_MS] * n)

    def long_blink(self):
        """The single long element — stop ('-')."""
        self._start_sequence([LONG_MS])

    def start_failure_pulse(self):
        """Persistent slow breathing until clear_failure() — the failure state."""
        self._stop_patterns()
        self._pulse_task = asyncio.create_task(self._failure_pulse_loop())

    def clear_failure(self):
        """Back to the quiet base (the LED off)."""
        self._stop_patterns()
        self._set_raw(False)

    async def _failure_pulse_loop(self):
        """Slow breathing: the duty steps 15→55→15, ~1.4 s per breath."""
        breath = (15, 25, 35, 45, 55, 45, 35, 25)
        try:
            while True:
                for duty in breath:
                    self._stop_pwm()
                    self._pwm = PWM(self.pin, freq=1000, duty_u16=int(self._scale(duty) * 655.35))
                    await asyncio.sleep_ms(120)
        except asyncio.CancelledError:
            pass
