import uasyncio as asyncio
from machine import Pin
import time

class Pushbutton:
    def __init__(self, pin_num, pin_pull=Pin.PULL_UP, active_low=True, action_name="unknown", callback=None):
        # Configure the hardware pin
        self.pin = Pin(pin_num, Pin.IN, pin_pull)
        self.active_low = active_low
        
        # Timing thresholds (in milliseconds)
        self.debounce_ms = 20
        self.long_press_ms = 500
        self.double_press_ms = 300
        
        # Callback placeholders
        self.on_press = callback

        self.action_name = action_name


        # Start the background async monitor
        asyncio.create_task(self._monitor())

    def _get_state(self):
        # Returns True if pressed, False if released
        # Accounts for active-low (pull-up) or active-high (pull-down)
        return self.pin.value() == 0 if self.active_low else self.pin.value() == 1

    def _trigger(self, press_type):
        """Helper to safely invoke the callback with context"""
        if self.on_press:
            # We pass the action name and the type of press back to the handler
            self.on_press(self.action_name, press_type)


    async def _monitor(self):
        while True:
            if self._get_state():
                press_time = time.ticks_ms()
                await asyncio.sleep_ms(self.debounce_ms)
                
                while self._get_state():
                    await asyncio.sleep_ms(10)
                
                release_time = time.ticks_ms()
                duration = time.ticks_diff(release_time, press_time)
                await asyncio.sleep_ms(self.debounce_ms)
                
                # Evaluate Press Type
                if duration >= self.long_press_ms:
                    self._trigger("long")
                else:
                    is_double = False
                    wait_until = time.ticks_add(time.ticks_ms(), self.double_press_ms)
                    
                    while time.ticks_diff(wait_until, time.ticks_ms()) > 0:
                        if self._get_state():
                            is_double = True
                            break
                        await asyncio.sleep_ms(10)
                    
                    if is_double:
                        await asyncio.sleep_ms(self.debounce_ms)
                        while self._get_state():
                            await asyncio.sleep_ms(10)
                        self._trigger("double")
                    else:
                        self._trigger("single")
                            
            await asyncio.sleep_ms(20)