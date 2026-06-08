from machine import Pin
import time

class InputController:
    """Unified handler for all physical inputs: buttons and switches.
    
    Combines button presses and microswitch into a single polling interface.
    All inputs use the same debounce logic and return their action type.
    Can operate in dummy mode for testing without hardware.
    """
    
    # Input action constants
    PLAY_PAUSE = "play_pause"
    NEXT = "next"
    PREV = "prev"
    STOP = "stop"
    NFC_CARD = "nfc_card"  # Microswitch
    KY040_PUSH = "ky040_push"  # KY-040 encoder pushbutton
    
    def __init__(self, button_pins=None,  debounce_ms=100, dummy_mode=False):  #microswitch_pin=17, encoder_sw_pin=33,
        """Initialize all inputs.
        
        Args:
            button_pins: Dict with button names and GPIO numbers
                        Default: {
                            'play_pause': 21,
                            'next': 22,
                            'prev': 25,
                            'stop': 26
                        }
            microswitch_pin: GPIO pin for microswitch (default 17)
            encoder_sw_pin: GPIO pin for KY-040 encoder pushbutton (default 33)
            debounce_ms: Debounce time in milliseconds (default 100ms)
            dummy_mode: If True, don't read actual button states (for testing without hardware)
        """
        if button_pins is None:
            button_pins = {
                'play_pause': 21,
                'next': 22,
                'prev': 25,
                'stop': 26
            }
        
        self.debounce_ms = debounce_ms
        self.dummy_mode = dummy_mode
        self.inputs = {}
        self.last_press_time = {}
        self.last_state = {}
        
        if dummy_mode:
            print(f"LOG: Input controller initialized in DUMMY MODE (no button reads)")
            return
        
        # Initialize buttons (only if not in dummy mode)
        for name, gpio_num in button_pins.items():
            try:
                pin = Pin(gpio_num, Pin.IN, Pin.PULL_UP)
                self.inputs[name] = {'pin': pin, 'type': 'button'}
                self.last_press_time[name] = 0
                self.last_state[name] = 1  # 1 = not pressed (pull-up)
                print(f"LOG: Input '{name}' initialized on GPIO {gpio_num}")
            except Exception as e:
                print(f"LOG: Failed to init input '{name}': {e}")
        

    
    def check_inputs(self):
        """Check all inputs for presses (non-blocking).
        
        Returns:
            str: Input name if pressed (e.g., 'play_pause', 'nfc_card'), None otherwise
        """
        if self.dummy_mode:
            return None  # Dummy mode: always return None (no inputs)
        
        current_time = time.ticks_ms()
        
        for name, input_cfg in self.inputs.items():
            pin = input_cfg['pin']
            current_state = pin.value()
            
            # Detect falling edge (NOT pressed = 1, pressed = 0 with pull-up)
            if current_state == 0 and self.last_state[name] == 1:
                # Transition from not-pressed to pressed
                last_press = self.last_press_time[name]
                if time.ticks_diff(current_time, last_press) > self.debounce_ms:
                    self.last_press_time[name] = current_time
                    print(f"LOG: Input pressed: {name}")
                    self.last_state[name] = current_state
                    return name
            
            self.last_state[name] = current_state
        
        return None
