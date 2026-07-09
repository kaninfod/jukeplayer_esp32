# jukeplayer/lib/hardware_factory.py
from jukeplayer.core.logger import log
from machine import Pin, SPI, I2C

class HardwareFactory:
    def __init__(self, config):
        self.config = config.get("hardware", {})
        
    def get_oled(self):
        cfg = self.config.get("oled", {})
        if not cfg.get("enabled", True):
            log.info("OLED Scroller: Initializing in DUMMY mode")
            # pyrefly: ignore [missing-import]
            from jukeplayer.mocks.dummy_oled import DummyOLEDScroller
            return DummyOLEDScroller()
            
        try:
            from jukeplayer.hardware.oled_manager import OLEDScroller
            # Setup I2C dynamically from config
            log.info(f"OLED Scroller: Initializing with config: {cfg}")
            sda_pin = Pin(cfg.get("sda", 13))
            scl_pin = Pin(cfg.get("scl", 14))
            i2c_unit = cfg.get("i2c_unit", 0)
            i2c = I2C(i2c_unit, sda=sda_pin, scl=scl_pin, freq=cfg.get("freq", 400000))
            
            return OLEDScroller(i2c)
        except Exception as e:
            log.error(f"Failed to init physical OLED: {e}. Falling back to Dummy OLED.")
            from jukeplayer.mocks.dummy_oled import DummyOLEDScroller
            return DummyOLEDScroller()

    def get_nfc(self):
        cfg = self.config.get("nfc_reader", {})
        if not cfg.get("enabled", True):
            log.info("NFC Reader: Initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_nfc import DummyNFCReader
            return DummyNFCReader()
            
        try:
            from jukeplayer.hardware.nfc_reader import NFCReader
            # Setup SPI dynamically from config
            log.info(f"NFC Reader: Initializing with config: {cfg}")
            spi = SPI(
                cfg.get("spi_unit", 1),
                baudrate=cfg.get("baudrate", 4000000),
                sck=Pin(cfg.get("sck", 18)),
                mosi=Pin(cfg.get("mosi", 23)),
                miso=Pin(cfg.get("miso", 19))
            )
            return NFCReader(
                spi,
                rst_pin=cfg.get("reset", 4),
                cs_pin=cfg.get("cs", 5)
            )
        except Exception as e:
            log.error(f"Failed to init physical NFC: {e}. Falling back to Dummy NFC.")
            from jukeplayer.mocks.dummy_nfc import DummyNFCReader
            return DummyNFCReader()

    def get_encoder(self):
        cfg = self.config.get("encoder", {})
        if not cfg.get("enabled", True):
            log.info("Rotary Encoder: Initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_rotary import DummyRotaryIRQ
            return DummyRotaryIRQ()
            
        try:
            from jukeplayer.hardware.rotary_irq_esp import RotaryIRQ
            log.debug(f"Rotary Encoder: Initializing with config: {cfg}")
            return RotaryIRQ(
                pin_num_clk=cfg.get("clk", 27),
                pin_num_dt=cfg.get("dt", 25),
                min_val=0,
                max_val=100,
                incr=3,
                reverse=False,
                range_mode=RotaryIRQ.RANGE_BOUNDED
            )
        except Exception as e:
            log.error(f"Failed to init physical encoder: {e}. Falling back to Dummy Encoder.")
            from jukeplayer.mocks.dummy_rotary import DummyRotaryIRQ
            return DummyRotaryIRQ()


    
    def get_pushbuttons(self):
        cfg = self.config.get("buttons", {})
        if not cfg.get("enabled", True):
            log.info("Buttons: Initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_input import DummyInputController
            return DummyInputController()
            
        try:
            from jukeplayer.hardware.pushbutton import Pushbutton

            log.debug(f"Pushbuttons: Initializing with config: {cfg.get("pins")}")
            pushbuttons = []
            
            for action_name, button_cfg in cfg.get("pins", {}).items():
                if isinstance(button_cfg, dict):
                    pin_num = button_cfg.get("pin", button_cfg.get("pin_num"))
                    if pin_num is None:
                        raise ValueError(f"Pushbutton '{action_name}' is missing required 'pin'")

                    pin_pull = button_cfg.get("pin_pull", Pin.PULL_UP)
                    if isinstance(pin_pull, str):
                        pull_lookup = {
                            "PULL_UP": Pin.PULL_UP,
                            "PULL_DOWN": Pin.PULL_DOWN,
                            "None": None,
                            "none": None,
                        }
                        pin_pull = pull_lookup.get(pin_pull, Pin.PULL_UP)

                    active_low = button_cfg.get("active_low", True)
                    configured_action_name = button_cfg.get("action_name", action_name)
                else:
                    pin_num = button_cfg
                    pin_pull = Pin.PULL_UP
                    active_low = True
                    configured_action_name = action_name

                log.debug(f"Pushbutton: Initializing pin {pin_num}, and named {configured_action_name}")
                pb = Pushbutton(
                    pin_num,
                    pin_pull=pin_pull,
                    active_low=active_low,
                    action_name=configured_action_name,
                )
                pushbuttons.append(pb)
                
            return pushbuttons
        except Exception as e:
            log.error(f"Failed to init physical buttons: {e}. Falling back to Dummy Buttons.")
            from jukeplayer.mocks.dummy_input import DummyInputController
            return DummyInputController()
