from machine import Pin
import time

# ============================================================================
# KY-040 ROTARY ENCODER - CALIBRATION PARAMETERS
# ============================================================================
# Adjust these values to tune the encoder behavior for your setup

MIN_VALUE = 0            # Minimum volume value
MAX_VALUE = 100          # Maximum volume value
STEP_SIZE = 2            # Volume increment per encoder step (1-10 recommended)

# ============================================================================
# QUADRATURE STATE TABLE
# ============================================================================
# Determines direction from state transitions: (prev_state -> curr_state)
# Each state is encoded as (CLK, DT) = 2-bit value: 00, 01, 10, 11
# 
# State transitions:
# CW:  00->10->11->01->00  (or any valid 2-state sequence indicating CW motion)
# CCW: 00->01->11->10->00  (or any valid 2-state sequence indicating CCW motion)
#
# Valid CW transitions (increment):  1,2,4,8,7,11,13,14
# Valid CCW transitions (decrement): 2,1,8,4,11,7,14,13
# All others are bounce/invalid

# Quadrature state transition table
# Index: (prev_state << 2) | curr_state
# Value: 1=CW, -1=CCW, 0=invalid/bounce
STATE_TABLE = [
    0,   # 00->00 (no change)
   -1,   # 00->01 (CCW start)
    1,   # 00->10 (CW start)
    0,   # 00->11 (invalid)
    1,   # 01->00 (CW continuation)
    0,   # 01->01 (no change)
    0,   # 01->10 (invalid - diagonal)
   -1,   # 01->11 (CCW continuation)
   -1,   # 10->00 (CCW continuation)
    0,   # 10->01 (invalid - diagonal)
    0,   # 10->10 (no change)
    1,   # 10->11 (CW continuation)
    0,   # 11->00 (invalid)
    1,   # 11->01 (CW continuation)
   -1,   # 11->10 (CCW continuation)
    0,   # 11->11 (no change)
]

# ============================================================================


class KY040Controller:
    """Handle KY-040 rotary encoder for volume control using hardware interrupts.
    
    Uses quadrature state table with IRQ handlers for instantaneous, bounce-resistant
    direction detection. Both CLK and DT pins trigger interrupts.
    
    The KY-040 has:
    - CLK: Clock pin (encoder signal A)
    - DT: Data pin (encoder signal B)
    - SW: Pushbutton (handled by InputController separately)
    """
    
    def __init__(self, clk_pin=27, dt_pin=16, dummy_mode=False):
        """Initialize KY-040 rotary encoder with interrupt handlers.
        
        Args:
            clk_pin: GPIO pin for CLK (default 27)
            dt_pin: GPIO pin for DT (default 16)
            dummy_mode: If True, don't attach interrupts (for testing)
        """
        self.clk_pin_num = clk_pin
        self.dt_pin_num = dt_pin
        self.dummy_mode = dummy_mode
        
        # Current volume/value tracking
        self.current_value = 50  # Start at 50% volume
        self.last_reported_value = self.current_value
        self.prev_state = 0  # Previous quadrature state
        self.rotation_direction = None  # 'cw' or 'ccw'
        self.volume_changed = False  # Flag: set by ISR when volume changes
        self.last_change_time = 0  # Timestamp of last volume change (for debouncing API calls), cleared by app
        
        if dummy_mode:
            print(f"LOG: KY-040 encoder initialized in DUMMY MODE (no hardware reads)")
        else:
            try:
                self.clk = Pin(clk_pin, Pin.IN, Pin.PULL_UP)
                self.dt = Pin(dt_pin, Pin.IN, Pin.PULL_UP)
                
                # Get initial state: (CLK, DT) as 2-bit value
                self.prev_state = (self.clk.value() << 1) | self.dt.value()
                
                # Attach interrupt handlers to both pins
                # Trigger on any edge (rising or falling)
                self.clk.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._on_encoder_change)
                self.dt.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._on_encoder_change)
                
                print(f"LOG: KY-040 encoder initialized with IRQs - CLK={clk_pin}, DT={dt_pin}")
            except Exception as e:
                print(f"LOG: ERROR initializing KY-040 encoder: {e}")
                self.dummy_mode = True
    
    def _on_encoder_change(self, pin):
        """ISR: Called on any CLK or DT edge. Process quadrature state change."""
        try:
            # Read current state: (CLK, DT) as 2-bit value
            curr_state = (self.clk.value() << 1) | self.dt.value()
            
            # Look up transition in state table
            # Index = (prev_state << 2) | curr_state
            table_index = (self.prev_state << 2) | curr_state
            direction = STATE_TABLE[table_index]
            
            # Update previous state for next transition
            self.prev_state = curr_state
            
            # Process valid transitions
            if direction == 1:  # CW
                new_value = min(self.current_value + STEP_SIZE, MAX_VALUE)
                self.rotation_direction = 'cw'
                self.current_value = new_value
                
                if self.current_value != self.last_reported_value:
                    self.last_reported_value = self.current_value
                    self.volume_changed = True  # Signal main loop to send API update
                    self.last_change_time = time.ticks_ms()  # Track time for debouncing
                    print(f"LOG: Encoder rotation: {self.rotation_direction} -> Volume: {self.current_value}%")
            
            elif direction == -1:  # CCW
                new_value = max(self.current_value - STEP_SIZE, MIN_VALUE)
                self.rotation_direction = 'ccw'
                self.current_value = new_value
                
                if self.current_value != self.last_reported_value:
                    self.last_reported_value = self.current_value
                    self.volume_changed = True  # Signal main loop to send API update
                    self.last_change_time = time.ticks_ms()  # Track time for debouncing
                    print(f"LOG: Encoder rotation: {self.rotation_direction} -> Volume: {self.current_value}%")
            
            # direction == 0: invalid/bounce - ignore
            
        except Exception as e:
            print(f"LOG: Error in encoder ISR: {e}")
    
    def should_send_api_update(self, debounce_ms=300):
        """Check if enough time has passed to send API update (debounce window).
        
        Returns:
            bool: True if debounce window has elapsed, False otherwise
        """
        if not self.volume_changed:
            return False
        
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.last_change_time)
        return elapsed >= debounce_ms
    
    def get_value(self):
        """Get current volume value.
        
        Returns:
            int: Current volume (0-100)
        """
        return self.current_value
    
    def set_value(self, value):
        """Set volume to specific value (for initialization or testing).
        
        Args:
            value: Volume value (0-100)
        """
        self.current_value = max(MIN_VALUE, min(MAX_VALUE, value))
        print(f"LOG: Encoder value set to {self.current_value}%")
