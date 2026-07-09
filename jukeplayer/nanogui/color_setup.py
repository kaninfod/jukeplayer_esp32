# """
# Hardware configuration for nano-gui
# JukePlayer ESP32 with 1.8" ST7735 128x160 display

# Configuration is loaded from config.json - see display section for pins
# """

# from machine import Pin, SPI
# import json

# # Load config to get display pins
# try:
#     with open("config.json", "r") as f:
#         config = json.load(f)
#     display_cfg = config.get("display", {})
# except:
#     # Fallback defaults if config not available
#     display_cfg = {
#         "sck": 14,
#         "mosi": 13,
#         "dc": 2,
#         "cs": 15,
#         "reset": 12,
#         "spi_unit": 2,
#         "baudrate": 4000000,
#         "phase": 0,
#         "polarity": 0
#     }

# # Import the ST7735 driver
# try:
#     # ST7735R driver for nano-gui
#     from jukeplayer.drivers.st7735r.st7735r import ST7735R as DRIVER
    
#     # Create SPI instance on SPI2 (VSPI)
#     # Display is write-only, so MISO not used (set to None)
#     spi = SPI(display_cfg.get("spi_unit", 2), 
#               baudrate=display_cfg.get("baudrate", 4000000), 
#               polarity=display_cfg.get("polarity", 0), 
#               phase=display_cfg.get("phase", 0),
#               mosi=Pin(display_cfg.get("mosi", 13)), 
#               sck=Pin(display_cfg.get("sck", 14)), 
#               miso=None)

#     # Create display instance (128x160 for ST7735)
#     ssd = DRIVER(spi, 
#                   cs=Pin(display_cfg.get("cs", 15), Pin.OUT),
#                   dc=Pin(display_cfg.get("dc", 2), Pin.OUT),
#                   rst=Pin(display_cfg.get("reset", 12), Pin.OUT),
#                   width=160,
#                   height=128)
    
#     # nano-gui expects uppercase SSD
#     SSD = ssd
# except Exception as e:
#     print(f"Error initializing ST7735R: {e}")
#     raise


# jukeplayer/color_setup.py
# Mock display class to satisfy nano-gui's color system for monochrome OLED

class MockSSD:
    @staticmethod
    def rgb(r, g, b):
        # Returns 1 for bright/white, 0 for dark/black
        return int((r > 127) or (g > 127) or (b > 127))

SSD = MockSSD
ssd = SSD
