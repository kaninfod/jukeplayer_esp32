# LVGL display backend for JukePlayer

This directory contains a replacement display backend that uses **LVGL** for
UI rendering and a small **C driver** for the ILI9488 panel.

It targets the **master** branch of `lv_binding_micropython`, which tracks
**LVGL v9** and is maintained for current MicroPython versions (v1.27+).
The Python code uses the v9 API (`lv.screen_active`, `lv.display_create`, etc.).

## What is here

- `display_manager.py` — Python `DisplayManager` + `StatusScreen` using LVGL
  widgets. It mirrors the API of the existing nano-gui managers so `app.py`
  does not need to change.
- `ili9488_driver.c` — User C module that registers an LVGL display driver,
  handles panel init, SPI speed switching, NFC CS deassertion, and
  RGB565 → RGB666 conversion.
- `micropython.cmake` — CMake file to compile the C module.

## What is *not* here

LVGL itself. You must add `lv_binding_micropython` to the MicroPython tree
before this module can compile.

## Adding lv_binding_micropython

From the `micropython/ports/esp32` directory:

```bash
cd /Users/martinhinge/projects/esp32_build/micropython/ports/esp32
git submodule add --depth 1 \
  https://github.com/lvgl/lv_binding_micropython.git \
  lv_binding_micropython
cd lv_binding_micropython
git checkout master
git submodule update --init --recursive
```

The `master` branch tracks LVGL v9.

Then copy the project-specific `lv_conf.h` into the binding directory so the
build picks it up:

```bash
cp /Users/martinhinge/projects/jukeplayer/jukeplayer_esp32/jukeplayer/hardware/lvgl/lv_conf.h \
   /Users/martinhinge/projects/esp32_build/micropython/ports/esp32/lv_binding_micropython/lv_conf.h
```

Make sure `lv_binding_micropython` and its own submodules (the LVGL repo)
are fully initialized before building. The build script does not run
`git submodule update` for you.

You will also need an `lv_conf.h` for your board. The project ships one
(`lv_conf.h` in this directory) configured for 16-bit RGB565 color and the
Montserrat 16/20/24 fonts.

## Selecting the LVGL backend

Change `hardware.tft.driver` in `config.json`:

```json
"tft": {
  "enabled": true,
  "driver": "ili9488_lvgl",
  "cs": 5,
  "a0": 7,
  "reset": 4,
  "led": 15,
  "width": 480,
  "height": 320,
  "usd": false,
  "mirror": false,
  "color_invert": false
}
```

The other display paths (`ili9488` for nano-gui and `st7735r`) remain
available.

## Build changes

The build script must pass `USER_C_MODULES` pointing to this directory's
CMake file. The CMake file includes `lv_binding_micropython` internally, so
only one path is needed:

```bash
make BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=SPIRAM_OCT \
     FROZEN_MANIFEST=/project/manifest.py \
     USER_C_MODULES=/project/jukeplayer/hardware/lvgl/micropython.cmake
```

See the updated `build_s3.sh` in `esp32_build/`.

## Current limitations / next steps

1. **Fonts/icons**: Uses LVGL built-in Montserrat fonts and plain-text
   indicators (">", "||", "[]", "RPT", "NET"). LVGL symbol icons can be
   enabled later by configuring the symbol font in `lv_conf.h`.
2. **Cover art**: The album art area is currently a placeholder. A future
   version can download an RGB565 image from the backend and update an
   `lv.img` widget, or blit directly into the flush callback.
3. **Dual buffering**: The C driver uses a single partial buffer to save RAM.
   Enable `buf2` on boards with more internal RAM if you need smoother
   animation.
4. **Touch**: Not implemented. If you add a touch panel, register an LVGL
   input device in a second C module or feed events from Python.
