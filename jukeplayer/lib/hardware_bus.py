from machine import Pin, SPI, I2C

class HardwareBus:
    def __init__(self, config):
        self.config = config
        self.spi_nfc = None
        self.i2c_oled = None
        self.button_pins = None

    def get_nfc_spi(self):
        """Initializes and returns the SPI bus for NFC only when needed."""
        if not self.spi_nfc:
            nfc_cfg = self.config.get("nfc_reader", {})
            self.spi_nfc = SPI(
                nfc_cfg.get("spi_unit", 1),
                baudrate=nfc_cfg.get("baudrate", 4000000),
                sck=Pin(nfc_cfg.get("sck", 18)),
                mosi=Pin(nfc_cfg.get("mosi", 23)),
                miso=Pin(nfc_cfg.get("miso", 19))
            )
        return self.spi_nfc

    # def get_oled_i2c(self):
    #     """Initializes and returns the I2C bus for the OLED."""
    #     if not self.i2c_oled:
    #         oled_cfg = self.config.get("oled", {})
    #         # Return I2C instance here
    #         self.i2c_oled = I2C(
    #             oled_cfg.get("i2c_unit", 0),
    #             scl=Pin(oled_cfg.get("scl", 33)),
    #             sda=Pin(oled_cfg.get("sda", 32)),
    #             freq=oled_cfg.get("freq", 400000)
    #         )
    #     return self.i2c_oled



    def get_oled_i2c(self):
        """Initializes and returns the I2C bus for the OLED."""
        if not self.i2c_oled:
            oled_cfg = self.config.get("oled", {})
            # Return I2C instance here
            self.i2c_oled = I2C(
                oled_cfg.get("i2c_unit", 0),
                scl=Pin(oled_cfg.get("scl", 33)),
                sda=Pin(oled_cfg.get("sda", 32)),
                freq=oled_cfg.get("freq", 400000)
            )
        return self.i2c_oled

    def get_button_pins(self):
        """Initializes and returns the button pins."""
        if not self.button_pins:
            buttons_cfg = self.config.get("buttons", {})
            self.button_pins = {
                'play_pause': buttons_cfg.get('play_pause', 21),
                'next': buttons_cfg.get('next', 22),
                'prev': buttons_cfg.get('previous', 25),
                'stop': buttons_cfg.get('stop', 26),
                'nfc_card': buttons_cfg.get('microswitch_nfc_card', 17),
                'ky040_push': buttons_cfg.get('encoder_push', 34)
            }
        return self.button_pins
    
