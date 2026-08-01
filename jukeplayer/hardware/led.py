import asyncio
from machine import Pin

class LEDController:
    def __init__(self, pin_number: int, invert: bool = False):
        """
        Initializes an LED controller.
        :param pin_number: The GPIO pin the LED is connected to.
        :param invert: Set to True if your LED is active-LOW (turns on when pin is 0).
        """
        # Configure the GPIO pin as output
        self.pin = Pin(pin_number, Pin.OUT)
        self.invert = invert
        
        # Internal state tracking
        self._blink_task = None
        self.turn_off()

    def turn_on(self):
        """Turns the LED on solidly."""
        self._stop_blink_task()
        self.pin.value(0 if self.invert else 1)

    def turn_off(self):
        """Turns the LED off solidly."""
        self._stop_blink_task()
        self.pin.value(1 if self.invert else 0)

    def blink(self, interval_ms: int):
        """
        Starts blinking the LED asynchronously.
        :param interval_ms: Time in milliseconds for each half-cycle (on time / off time).
        """
        self._stop_blink_task()
        # Schedule the blink loop into the background asyncio loop
        self._blink_task = asyncio.create_task(self._blink_loop(interval_ms))

    async def _blink_loop(self, interval_ms: int):
        """The background loop that handles toggling the pin."""
        try:
            while True:
                # Toggle the current state of the pin
                self.pin.value(not self.pin.value())
                # Yield control to allow other LEDs and tasks to process
                await asyncio.sleep_ms(interval_ms)
        except asyncio.CancelledError:
            # Clean up gracefully if the task is cancelled
            pass

    def _stop_blink_task(self):
        """Stops any active background blinking task."""
        if self._blink_task is not None:
            self._blink_task.cancel()
            self._blink_task = None