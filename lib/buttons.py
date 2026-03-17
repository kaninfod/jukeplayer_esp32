from machine import Pin
import time

class ButtonController:
    """Handle 4 control buttons with debouncing."""
    
    # Button action callbacks
    PLAY_PAUSE = "play_pause"
    NEXT = "next"
    PREV = "prev"
    STOP = "stop"
    
    def __init__(self, pins=None):
        """Initialize buttons.
        
        Args:
            pins: Dict with button names and GPIO numbers
                  Default: {
                      'play_pause': 21,
                      'next': 22,
                      'prev': 25,
                      'stop': 26
                  }
        """
        if pins is None:
            pins = {
                'play_pause': 21,
                'next': 22,
                'prev': 25,
                'stop': 26
            }
        
        self.pins = pins
        self.buttons = {}
        self.last_press_time = {}
        self.debounce_ms = 200  # 200ms debounce
        
        # Initialize pin callbacks
        self._init_buttons()
    
    def _init_buttons(self):
        """Initialize button pins and attach interrupts."""
        for name, gpio_num in self.pins.items():
            try:
                pin = Pin(gpio_num, Pin.IN, Pin.PULL_UP)
                self.buttons[name] = pin
                self.last_press_time[name] = 0
                print(f"LOG: Button '{name}' initialized on GPIO {gpio_num}")
            except Exception as e:
                print(f"LOG: Failed to init button '{name}': {e}")
    
    def check_buttons(self):
        """Check all buttons for presses (non-blocking).
        
        Returns:
            str: Button name if pressed, None otherwise
        """
        current_time = time.ticks_ms()
        
        for name, pin in self.buttons.items():
            # Button pressed = LOW (pull-up means not pressed = HIGH)
            if pin.value() == 0:
                # Check debounce
                last_press = self.last_press_time[name]
                if time.ticks_diff(current_time, last_press) > self.debounce_ms:
                    self.last_press_time[name] = current_time
                    print(f"LOG: Button pressed: {name}")
                    return name
        
        return None
