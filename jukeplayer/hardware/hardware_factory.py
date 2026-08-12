# jukeplayer/lib/hardware_factory.py
from jukeplayer.core.logger import log
from machine import Pin, SPI, I2C

class HardwareFactory:
    def __init__(self, config):
        self._parent_config = config
        self.config = config.get("hardware", {})
        self._shared_spi = None
        self._shared_spi_cfg = None
        self._shared_spi_kwargs = None
        self._init_shared_spi()

    def _init_shared_spi(self):
        """Initialize one shared SPI bus from hardware.spi config."""
        spi_cfg = self.config.get("spi", {})
        required = ("spi_unit", "sck", "mosi")
        missing = [k for k in required if spi_cfg.get(k) is None]
        if missing:
            raise ValueError(f"hardware.spi missing required keys: {', '.join(missing)}")

        base_baudrate = int(spi_cfg.get("baudrate", 4000000))
        self._display_baudrate = int(spi_cfg.get("display_baudrate", base_baudrate))
        self._nfc_baudrate = int(spi_cfg.get("nfc_baudrate", base_baudrate))

        self._shared_spi_cfg = {
            "spi_unit": int(spi_cfg.get("spi_unit")),
            "baudrate": base_baudrate,
            "polarity": int(spi_cfg.get("polarity", 0)),
            "phase": int(spi_cfg.get("phase", 0)),
            "sck": int(spi_cfg.get("sck")),
            "mosi": int(spi_cfg.get("mosi")),
            "miso": int(spi_cfg.get("miso")) if spi_cfg.get("miso") is not None else None,
        }

        p_sck = Pin(self._shared_spi_cfg["sck"])
        p_mosi = Pin(self._shared_spi_cfg["mosi"])
        p_miso = Pin(self._shared_spi_cfg["miso"]) if self._shared_spi_cfg["miso"] is not None else None

        kwargs = {
            "baudrate": self._shared_spi_cfg["baudrate"],
            "polarity": self._shared_spi_cfg["polarity"],
            "phase": self._shared_spi_cfg["phase"],
            "sck": p_sck,
            "mosi": p_mosi,
        }
        if p_miso is not None:
            kwargs["miso"] = p_miso

        self._shared_spi = SPI(self._shared_spi_cfg["spi_unit"], **kwargs)
        self._shared_spi_kwargs = kwargs
        log.info(
            f"[SPI] shared bus initialized id={id(self._shared_spi)} unit={self._shared_spi_cfg['spi_unit']}"
        )

    def _get_shared_spi(self):
        if self._shared_spi is None or self._shared_spi_kwargs is None:
            self._init_shared_spi()

        spi = self._shared_spi
        kwargs = self._shared_spi_kwargs
        if spi is None or kwargs is None:
            raise RuntimeError("Shared SPI not initialized")
        spi.init(**kwargs)
        return spi

    def _get_spi_kwargs(self, baudrate=None):
        """Return a copy of the shared SPI kwargs with an optional baudrate override."""
        kwargs = dict(self._shared_spi_kwargs)
        if baudrate is not None:
            kwargs["baudrate"] = baudrate
        return kwargs
        
    def get_display(self, app_state):
        tft_cfg = self.config.get("tft", {})
        if tft_cfg.get("enabled", False):
            log.info("Display factory: selecting TFT display")
            return self.get_tft_display(app_state=app_state)

        cfg = self.config.get("oled", {})
        if not cfg.get("enabled", True):
            log.info("OLED Scroller: Initializing in DUMMY mode")
            # pyrefly: ignore [missing-import]
            from jukeplayer.mocks.dummy_oled import DummyOLEDScroller
            return DummyOLEDScroller()
            
        try:
            from jukeplayer.hardware.display_manager import DisplayManager
            log.info(f"OLED Display manager: Initializing with config: {cfg}")
            sda_pin = Pin(cfg.get("sda", 13))
            scl_pin = Pin(cfg.get("scl", 14))
            i2c_unit = cfg.get("i2c_unit", 0)
            i2c = I2C(i2c_unit, sda=sda_pin, scl=scl_pin, freq=cfg.get("freq", 400000))
            
            return DisplayManager(i2c, app_state=app_state)
        except Exception as e:
            log.error(f"Failed to init physical OLED: {e}. Falling back to Dummy OLED.")
            from jukeplayer.mocks.dummy_oled import DummyOLEDScroller
            return DummyOLEDScroller()

    def get_tft_display(self, app_state):
        """Initialize TFT display manager selected by hardware.tft.driver."""
        cfg = self.config.get("tft", {})
        if not cfg.get("enabled", True):
            raise RuntimeError("TFT display is disabled in config")

        # Select TFT driver: "st7735r", "ili9488" (nano-gui), or "ili9488_lvgl".
        # TEMPORARY RECOVERY OVERRIDE: force old ili9488 driver while debugging LVGL.
        driver = "ili9488"
        # driver = cfg.get("driver", "st7735r")
        if driver == "ili9488":
            from jukeplayer.hardware.ili9488.display_manager import DisplayManager
        elif driver == "ili9488_lvgl":
            from jukeplayer.hardware.lvgl.display_manager import DisplayManager
        else:
            from jukeplayer.hardware.st7735r.display_manager import DisplayManager

        required = ("cs", "a0", "reset")
        missing = [k for k in required if cfg.get(k) is None]
        if missing:
            raise ValueError(f"TFT config missing required pins: {', '.join(missing)}")

        try:
            log.info(f"[TFT] init stage 1/5: preparing pins for driver '{driver}'")

            log.info("[TFT] pin init: cs")
            p_cs = Pin(cfg.get("cs"), Pin.OUT)
            p_cs.value(1)

            # If NFC shares this SPI bus, make sure its CS is deasserted.
            nfc_cfg = self.config.get("nfc_reader", {})
            if nfc_cfg.get("enabled", False):
                nfc_cs = nfc_cfg.get("cs")
                if nfc_cs is not None:
                    Pin(nfc_cs, Pin.OUT).value(1)

            log.info("[TFT] pin init: a0/dc")
            p_dc = Pin(cfg.get("a0"), Pin.OUT)
            p_dc.value(0)

            log.info("[TFT] pin init: reset")
            rst_num = int(cfg.get("reset"))
            if rst_num in (45, 46):
                log.info(f"[TFT] warning: reset pin {rst_num} is a strapping/special pin on ESP32-S3 and may be unreliable")

            p_rst = Pin(rst_num, Pin.OUT)
            p_rst.value(1)

            led_num = int(cfg.get("led", 21))


            log.info("[TFT] init stage 2/5: creating SPI bus")
            spi = self._get_shared_spi()
            shared_cfg = self._shared_spi_cfg
            if shared_cfg is None:
                raise RuntimeError("Shared SPI config missing")
            shared_unit = shared_cfg["spi_unit"]
            log.info(
                f"[TFT] shared SPI object id={id(spi)} unit={shared_unit}"
            )

            shared_kwargs = self._shared_spi_kwargs
            if shared_kwargs is None:
                raise RuntimeError("Shared SPI kwargs missing")

            def _tft_spi_init(spi_obj):
                display_kwargs = self._get_spi_kwargs(self._display_baudrate)
                spi_obj.init(**display_kwargs)
                nfc_cfg_local = self.config.get("nfc_reader", {})
                if nfc_cfg_local.get("enabled", False):
                    nfc_cs_local = nfc_cfg_local.get("cs")
                    if nfc_cs_local is not None:
                        Pin(nfc_cs_local, Pin.OUT).value(1)

            log.info("[TFT] init stage 3/5: creating DisplayManager")
            backend = self._parent_config.get("backend", {})
            cover_base_url = f"http://{backend.get('ip', '127.0.0.1')}:{backend.get('port', 8000)}"

            display_kwargs = {
                "spi": spi,
                "app_state": app_state,
                "cs": p_cs,
                "dc": p_dc,
                "rst": p_rst,
                "backlight_pin": led_num,
                "width": cfg.get("width", 160),
                "height": cfg.get("height", 128),
                "usd": cfg.get("usd", False),
                "mirror": cfg.get("mirror", False),
                "color_invert": cfg.get("color_invert", False),
                "cover_base_url": cover_base_url,
            }

            # LVGL driver handles SPI speed switching internally, so pass the
            # target baudrate and the NFC chip-select to keep deasserted.
            if driver == "ili9488_lvgl":
                display_kwargs["spi_baudrate"] = self._display_baudrate
                nfc_cfg = self.config.get("nfc_reader", {})
                if nfc_cfg.get("enabled", False):
                    nfc_cs_pin = nfc_cfg.get("cs")
                    if nfc_cs_pin is not None:
                        display_kwargs["nfc_cs"] = nfc_cs_pin
            else:
                display_kwargs["init_spi"] = _tft_spi_init

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
            log.info("NFC Reader: Initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_nfc import DummyNFCReader
            return DummyNFCReader()
            
        try:
            from jukeplayer.hardware.nfc_reader import NFCReader
            # Setup SPI dynamically from config
            log.info(f"NFC Reader: Initializing with config: {cfg}")

            # If TFT shares this SPI bus, make sure its CS is deasserted.
            tft_cfg = self.config.get("tft", {})
            if tft_cfg.get("enabled", False):
                tft_cs = tft_cfg.get("cs")
                if tft_cs is not None:
                    Pin(tft_cs, Pin.OUT).value(1)

            spi = self._get_shared_spi()
            shared_cfg = self._shared_spi_cfg
            if shared_cfg is None:
                raise RuntimeError("Shared SPI config missing")
            shared_unit = shared_cfg["spi_unit"]
            log.info(
                f"[NFC] shared SPI object id={id(spi)} unit={shared_unit}"
            )

            shared_kwargs = self._shared_spi_kwargs
            if shared_kwargs is None:
                raise RuntimeError("Shared SPI kwargs missing")

            def _nfc_spi_init(spi_obj):
                nfc_kwargs = self._get_spi_kwargs(self._nfc_baudrate)
                spi_obj.init(**nfc_kwargs)

            return NFCReader(
                spi,
                rst_pin=cfg.get("reset", 4),
                cs_pin=cfg.get("cs", 5),
                spi_init=_nfc_spi_init,
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

    def get_leds(self):
        cfg = self.config.get("leds", {})
        if not cfg.get("enabled", True):
            log.info("LEDs: Initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_led import DummyLEDController
            return DummyLEDController()
        try:
            from jukeplayer.hardware.led import LEDController
            pins = cfg.get("pins", {})
            leds = {}
            for name, pin in pins.items():
                leds[name] = LEDController(pin_number=pin)
            return leds
        except Exception as e:
            log.error(f"Failed to init physical LEDs: {e}. Falling back to Dummy LEDs.")
            from jukeplayer.mocks.dummy_led import DummyLEDController
            return DummyLEDController()
    
    def get_pushbuttons(self):
        cfg = self.config.get("buttons", {})
        if not cfg.get("enabled", True):
            log.info("Buttons: Initializing in DUMMY mode")
            from jukeplayer.mocks.dummy_input import DummyInputController
            return DummyInputController()
            
        try:
            from jukeplayer.hardware.pushbutton import Pushbutton

            log.debug(f"Pushbuttons: Initializing with config: {cfg.get('pins')}")
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
