import machine
from machine import I2C, Pin
import ssd1306

i2c = I2C(0, sda=Pin(32), scl=Pin(33), freq=400000)
display = ssd1306.SSD1306_I2C(128, 64, i2c)
print("Before fill_rect")
display.fill_rect(-1, 20, 128, 12, 1)
print("After fill_rect")
import gc
gc.collect()
print("Survived!")
