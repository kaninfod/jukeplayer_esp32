# Jukeplayer ESP32 Client

Lightweight hardware client for ESP32 microcontroller.

## Features

- Button control for music playback
- NFC/RFID card reader for album selection
- Small display (OLED/LCD) for track information
- WiFi connectivity to backend
- Minimal power consumption

## Hardware

- **Buttons**: GPIO pins for next/previous/play/pause
- **NFC Reader**: SPI/I2C NFC module
- **Display**: OLED or small LCD screen
- **LED**: Status indicator

## Setup

### Install MicroPython

1. Get MicroPython firmware for ESP32 from micropython.org
2. Flash using esptool or similar tool

### Configure WiFi

Create `config.py`:
```python
WIFI_SSID = "your_network"
WIFI_PASSWORD = "your_password"
BACKEND_URL = "http://192.168.1.100:8000"
BACKEND_WS_URL = "ws://192.168.1.100:8000/ws/mediaplayer"
```

### Upload Code

Use ampy or WebREPL to upload files to ESP32:
```bash
ampy --port /dev/ttyUSB0 put src/ /
```

## Code Structure

- `src/main.py`: Entry point
- `src/hardware/`: Button, NFC, display drivers
- `src/client/`: API and WebSocket client

## Running

Code runs automatically on ESP32 boot.

## Development Notes

- This is a placeholder for now
- You will copy your existing ESP32 code here
- The structure mirrors Jukeplayer_rpi for consistency
