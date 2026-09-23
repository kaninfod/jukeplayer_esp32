# jukeplayer/lib/hardware_factory.py
from jukeplayer.core.logger import log
from machine import Pin, SPI
from jukeplayer.hardware.spi_controller import SPIController

class HardwareFactory:
    def __init__(self, config):
        self._parent_config = config
        self.config = config.get("hardware", {})
        self.spi_ctl = SPIController(self.config)

    def get_display(self, app_state):
        tft_cfg = self.config.get("tft", {})
        if not tft_cfg.get("enabled", False):
            # Headless mode: the app runs without a display (diagnostics)
            log.info("[TFT] disabled — running headless (DummyDisplay)")
            from jukeplayer.mocks.dummy_display import DummyDisplay
            return DummyDisplay()
        log.info("[TFT] selecting driver")
        return self.get_tft_display(app_state=app_state)

    def get_tft_display(self, app_state):
        """Initialize TFT display manager selected by hardware.tft.driver."""
        cfg = self.config.get("tft", {})
        if not cfg.get("enabled", True):
            raise RuntimeError("TFT display is disabled in config")

        # Select TFT driver: only "ili9488" or "st7735r" are supported.
        driver = cfg.get("driver", "st7735r")
        if driver == "ili9488":
            from jukeplayer.hardware.ili9488.display_manager import DisplayManager
        elif driver == "st7735r":
            from jukeplayer.hardware.st7735r.display_manager import DisplayManager
        else:
            raise ValueError(f"Unsupported TFT driver '{driver}'. Use 'ili9488' or 'st7735r'.")

        required = ("cs", "a0", "reset")
        missing = [k for k in required if cfg.get(k) is None]
        if missing:
            raise ValueError(f"TFT config missing required pins: {', '.join(missing)}")

        try:
            log.info(f"[TFT] init stage 1/5: preparing pins for driver '{driver}'")

            log.debug("[TFT] pin init: cs")
            p_cs = Pin(cfg.get("cs"), Pin.OUT)
            p_cs.value(1)

            # If NFC shares this SPI bus, make sure its CS is deasserted.
            nfc_cfg = self.config.get("nfc_reader", {})
            if nfc_cfg.get("enabled", False):
                nfc_cs = nfc_cfg.get("cs")
                if nfc_cs is not None:
                    Pin(nfc_cs, Pin.OUT).value(1)

            log.debug("[TFT] pin init: a0/dc")
            p_dc = Pin(cfg.get("a0"), Pin.OUT)
            p_dc.value(0)

            log.debug("[TFT] pin init: reset")
            rst_num = int(cfg.get("reset"))
            if rst_num in (45, 46):
                log.warn(f"[TFT] reset pin {rst_num} is a strapping/special pin on ESP32-S3 and may be unreliable")

            p_rst = Pin(rst_num, Pin.OUT)
            p_rst.value(1)

            led_num = int(cfg.get("led", 21))


            log.info("[TFT] init stage 2/5: SPI bus managed by SPIController")
            spi = self.spi_ctl.spi

            log.info("[TFT] init stage 3/5: creating DisplayManager")
            backend = self._parent_config.get("backend", {})
            cover_base_url = f"http://{backend.get('ip', '127.0.0.1')}:{backend.get('port', 8000)}"

            display_kwargs = {
                "spi_ctl": self.spi_ctl,
                "app_state": app_state,
                "cs": p_cs,
                "dc": p_dc,
                "rst": p_rst,
                "backlight_pin": led_num,
                "backlight_active_low": cfg.get("backlight_active_low", True),
                "width": cfg.get("width", 160),
                "height": cfg.get("height", 128),
                # rotate_180 is an alias for the driver's usd (upside-down) flag.
                # Defaults to False so the display uses normal orientation.
                "usd": cfg.get("rotate_180", cfg.get("usd", False)),
                "mirror": cfg.get("mirror", False),
                "color_invert": cfg.get("color_invert", False),
                "cover_base_url": cover_base_url,
                "display_baudrate": int(cfg.get("baudrate", 24000000)),
            }
            effective_usd = display_kwargs["usd"]
            log.debug(f"[TFT] orientation config rotate_180={cfg.get('rotate_180', None)} usd={cfg.get('usd', None)} effective_usd={effective_usd}")

            # The SPIController handles the bus speed switching.
            display = DisplayManager(**display_kwargs)

            # log.info("[TFT] init stage 4/5: enabling backlight")
            # led_pin_num = cfg.get("led")
            # if led_pin_num is not None:
            #     backlight = Pin(led_pin_num, Pin.OUT)
            #     backlight.value(1)
            #     setattr(display, "_backlight_pin", backlight)

            log.info("[TFT] init stage 5/5: ready")
            return display
        except Exception as e:
            log.error(f"[TFT] init failed: {e}")
            raise

    def get_nfc(self):
        cfg = self.config.get("nfc_reader", {})
        if not cfg.get("enabled", True):
            log.info("[NFC] initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_nfc import DummyNFCReader
            return DummyNFCReader()
            
        try:
            from jukeplayer.hardware.nfc_reader import NFCReader
            # Setup SPI dynamically from config
            log.debug(f"[NFC] initializing with config: {cfg}")

            # If TFT shares this SPI bus, make sure its CS is deasserted.
            tft_cfg = self.config.get("tft", {})
            if tft_cfg.get("enabled", False):
                tft_cs = tft_cfg.get("cs")
                if tft_cs is not None:
                    Pin(tft_cs, Pin.OUT).value(1)

            log.debug(
                f"[NFC] shared SPI object id={id(self.spi_ctl.spi)} unit={self.spi_ctl._unit}"
            )

            return NFCReader(
                self.spi_ctl,
                rst_pin=cfg.get("reset", 4),
                cs_pin=cfg.get("cs", 5),
            )
        except Exception as e:
            log.error(f"Failed to init physical NFC: {e}. Falling back to Dummy NFC.")
            from jukeplayer.mocks.dummy_nfc import DummyNFCReader
            return DummyNFCReader()

    def get_encoder(self):
        cfg = self.config.get("encoder", {})
        if not cfg.get("enabled", True):
            log.info("[ENC] initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_rotary import DummyRotaryIRQ
            return DummyRotaryIRQ()
            
        try:
            from jukeplayer.hardware.rotary_irq_esp import RotaryIRQ
            log.debug(f"[ENC] initializing with config: {cfg}")
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
            log.error(f"[ENC] init failed: {e} — falling back to dummy encoder")
            from jukeplayer.mocks.dummy_rotary import DummyRotaryIRQ
            return DummyRotaryIRQ()

    def get_pushbuttons(self):
        cfg = self.config.get("buttons", {})
        if not cfg.get("enabled", True):
            log.info("[BTN] initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_input import DummyInputController
            return DummyInputController()
            
        try:
            from jukeplayer.hardware.pushbutton import Pushbutton

            log.debug(f"[BTN] initializing...")
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

                log.debug(f"[BTN] pin {pin_num} -> {configured_action_name}")
                pb = Pushbutton(
                    pin_num,
                    pin_pull=pin_pull,
                    active_low=active_low,
                    action_name=configured_action_name,
                )
                pushbuttons.append(pb)
                
            return pushbuttons
        except Exception as e:
            log.error(f"[BTN] init failed: {e} — falling back to dummy buttons")
            from jukeplayer.mocks.dummy_input import DummyInputController
            return DummyInputController()
