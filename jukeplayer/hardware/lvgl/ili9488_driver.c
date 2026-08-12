// ILI9488 display driver for LVGL v9 on MicroPython / ESP32.
//
// Compiled as a user C module. It creates an LVGL v9 display with a flush
// callback that talks directly to the panel over a shared machine.SPI bus,
// switching SPI speed and deasserting the NFC chip-select as needed.
//
// Color: LVGL renders internally as RGB565. The flush callback expands each
// pixel to RGB666 (18-bit) because the ILI9488 SPI interface requires 3
// bytes per pixel.

#include "py/runtime.h"
#include "py/stream.h"
#include "py/mphal.h"
#include "py/obj.h"

#include "lvgl.h"

// Helper for debug prints from C.
#define _INFO(fmt, ...)  mp_printf(&mp_plat_print, "[ILI9488] " fmt "\n", ##__VA_ARGS__)
#define _ERR(fmt, ...)   mp_printf(&mp_plat_print, "[ILI9488 ERR] " fmt "\n", ##__VA_ARGS__)

// ---------------------------------------------------------------------------
// Driver state
// ---------------------------------------------------------------------------

typedef struct {
    mp_obj_t spi;                       // machine.SPI object
    mp_hal_pin_obj_t cs;                // chip-select
    mp_hal_pin_obj_t dc;                // data/command
    mp_hal_pin_obj_t rst;               // reset
    mp_hal_pin_obj_t nfc_cs;            // NFC chip-select (kept high during display ops)
    bool has_nfc_cs;
    uint32_t baudrate;                  // Display SPI speed
    int32_t width;
    int32_t height;
    bool usd;
    bool mirror;
    bool color_invert;

    lv_display_t *disp;
    uint8_t *linebuf;                   // Temporary RGB666 line buffer
} ili9488_state_t;

static ili9488_state_t g_state;

// ---------------------------------------------------------------------------
// Low-level SPI helpers
// ---------------------------------------------------------------------------

static inline void _cs_low(void) {
    mp_hal_pin_od_low(g_state.cs);
}

static inline void _cs_high(void) {
    mp_hal_pin_od_high(g_state.cs);
}

static inline void _dc_cmd(void) {
    mp_hal_pin_od_low(g_state.dc);
}

static inline void _dc_data(void) {
    mp_hal_pin_od_high(g_state.dc);
}

static int _spi_write(const void *buf, size_t len) {
    int err = 0;
    mp_stream_write_exactly(g_state.spi, buf, len, &err);
    return err;
}

static void _write_cmd(uint8_t cmd) {
    _cs_low();
    _dc_cmd();
    _spi_write(&cmd, 1);
    _cs_high();
}

static void _write_data(const uint8_t *buf, size_t len) {
    _cs_low();
    _dc_data();
    _spi_write(buf, len);
    _cs_high();
}

static void _sleep_ms(uint32_t ms) {
    mp_hal_delay_ms(ms);
}

// Switch SPI to display speed and make sure NFC CS is high.
static void _prepare_display_spi(uint32_t baudrate) {
    // Deassert NFC chip-select if configured.
    if (g_state.has_nfc_cs) {
        mp_hal_pin_od_high(g_state.nfc_cs);
    }

    // Switch baudrate via spi.init(baudrate=...).
    mp_obj_t dest[2];
    mp_load_method(g_state.spi, MP_QSTR_init, dest);

    mp_obj_t args[3];
    args[0] = dest[1];                       // self
    args[1] = MP_OBJ_NEW_QSTR(MP_QSTR_baudrate);
    args[2] = mp_obj_new_int(baudrate);

    mp_call_function_n_kw(dest[0], 1, 1, args);
}

// ---------------------------------------------------------------------------
// ILI9488 initialization and window setup
// ---------------------------------------------------------------------------

static void _hw_reset(void) {
    mp_hal_pin_od_low(g_state.rst);
    _sleep_ms(50);
    mp_hal_pin_od_high(g_state.rst);
    _sleep_ms(50);
}

static void _set_addr_window(int32_t x1, int32_t y1, int32_t x2, int32_t y2) {
    uint8_t col[4] = {
        (uint8_t)(x1 >> 8), (uint8_t)(x1 & 0xFF),
        (uint8_t)(x2 >> 8), (uint8_t)(x2 & 0xFF)
    };
    uint8_t page[4] = {
        (uint8_t)(y1 >> 8), (uint8_t)(y1 & 0xFF),
        (uint8_t)(y2 >> 8), (uint8_t)(y2 & 0xFF)
    };

    _write_cmd(0x2A);  // Column address set
    _write_data(col, 4);

    _write_cmd(0x2B);  // Page address set
    _write_data(page, 4);
}

static void _init_sequence(void) {
    _INFO("init sequence start");
    _hw_reset();
    _INFO("after reset");

    _prepare_display_spi(4000000);  // Use slow 4 MHz for init commands.
    _INFO("spi switched to 4 MHz for init");

    _write_cmd(0x01);  // SWRESET
    _sleep_ms(100);

    _write_cmd(0x11);  // Sleep out
    _sleep_ms(20);

    _write_cmd(0x3A);  // Interface pixel format
    {
        uint8_t fmt = 0x66;  // 18 bits / pixel
        _write_data(&fmt, 1);
    }

    // Column/page range defaults to full screen.
    {
        uint8_t full[4] = {0, 0, (uint8_t)((g_state.width - 1) >> 8), (uint8_t)((g_state.width - 1) & 0xFF)};
        _write_cmd(0x2A);
        _write_data(full, 4);
    }
    {
        uint8_t full[4] = {0, 0, (uint8_t)((g_state.height - 1) >> 8), (uint8_t)((g_state.height - 1) & 0xFF)};
        _write_cmd(0x2B);
        _write_data(full, 4);
    }

    // Memory access control.
    uint8_t madctl;
    if (g_state.width > g_state.height) {
        // Landscape
        madctl = g_state.usd ? 0xE8 : 0x28;
    } else {
        // Portrait
        madctl = g_state.usd ? 0x48 : 0x88;
    }
    if (g_state.mirror) {
        madctl ^= 0x80;
    }
    _write_cmd(0x36);
    _write_data(&madctl, 1);

    _write_cmd(0x11);  // Sleep out
    _write_cmd(0x29);  // Display on
    _INFO("init sequence complete");
}

// ---------------------------------------------------------------------------
// LVGL v9 flush callback
// ---------------------------------------------------------------------------

static void ili9488_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map) {
    (void)disp;

    int32_t w = area->x2 - area->x1 + 1;
    int32_t h = area->y2 - area->y1 + 1;
    int32_t x1 = area->x1;
    int32_t y1 = area->y1;

    _prepare_display_spi(g_state.baudrate);
    _set_addr_window(x1, y1, area->x2, area->y2);
    _write_cmd(0x2C);  // Memory write

    // px_map is RGB565 data (2 bytes per pixel). Convert to RGB666 and send.
    uint8_t *lb = g_state.linebuf;
    size_t row_rgb666_len = (size_t)w * 3;

    _cs_low();
    _dc_data();

    for (int32_t row = 0; row < h; row++) {
        uint8_t *src = px_map + (size_t)row * w * 2;
        uint8_t *dst = lb;

        for (int32_t col = 0; col < w; col++) {
            uint16_t c = (uint16_t)src[0] | ((uint16_t)src[1] << 8);
            src += 2;

            uint8_t r5 = (c >> 11) & 0x1F;
            uint8_t g6 = (c >> 5) & 0x3F;
            uint8_t b5 = c & 0x1F;

            // Expand 565 -> 666.
            *dst++ = (r5 << 1) | (r5 >> 4);   // R
            *dst++ = g6;                       // G
            *dst++ = (b5 << 1) | (b5 >> 4);   // B
        }

        int err = _spi_write(lb, row_rgb666_len);
        if (err != 0) {
            _cs_high();
            lv_display_flush_ready(disp);
            return;
        }
    }

    _cs_high();
    lv_display_flush_ready(disp);
}

// ---------------------------------------------------------------------------
// MicroPython module: ili9488_lvgl
// ---------------------------------------------------------------------------

static mp_obj_t ili9488_lvgl_init(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum {
        ARG_spi, ARG_cs, ARG_dc, ARG_rst,
        ARG_width, ARG_height,
        ARG_usd, ARG_mirror, ARG_color_invert,
        ARG_baudrate, ARG_nfc_cs, ARG_buffer_lines
    };

    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_spi,            MP_ARG_REQUIRED | MP_ARG_OBJ,  {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_cs,             MP_ARG_REQUIRED | MP_ARG_OBJ,  {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_dc,             MP_ARG_REQUIRED | MP_ARG_OBJ,  {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_rst,            MP_ARG_REQUIRED | MP_ARG_OBJ,  {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_width,          MP_ARG_REQUIRED | MP_ARG_INT,   {.u_int = 480} },
        { MP_QSTR_height,         MP_ARG_REQUIRED | MP_ARG_INT,   {.u_int = 320} },
        { MP_QSTR_usd,            MP_ARG_BOOL,                   {.u_bool = false} },
        { MP_QSTR_mirror,         MP_ARG_BOOL,                   {.u_bool = false} },
        { MP_QSTR_color_invert,   MP_ARG_BOOL,                   {.u_bool = false} },
        { MP_QSTR_baudrate,       MP_ARG_INT,                    {.u_int = 24000000} },
        { MP_QSTR_nfc_cs,         MP_ARG_OBJ,                    {.u_obj = mp_const_none} },
        { MP_QSTR_buffer_lines,   MP_ARG_INT,                    {.u_int = 40} },
    };

    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    g_state.spi = args[ARG_spi].u_obj;
    g_state.cs = mp_hal_get_pin_obj(args[ARG_cs].u_obj);
    g_state.dc = mp_hal_get_pin_obj(args[ARG_dc].u_obj);
    g_state.rst = mp_hal_get_pin_obj(args[ARG_rst].u_obj);

    if (args[ARG_nfc_cs].u_obj == mp_const_none) {
        g_state.has_nfc_cs = false;
    } else {
        g_state.has_nfc_cs = true;
        mp_obj_t nfc_cs_obj = args[ARG_nfc_cs].u_obj;
        if (mp_obj_is_int(nfc_cs_obj)) {
            nfc_cs_obj = mp_pin_make_new(NULL, 1, 0, &nfc_cs_obj);
        }
        g_state.nfc_cs = mp_hal_get_pin_obj(nfc_cs_obj);
    }

    g_state.width = args[ARG_width].u_int;
    g_state.height = args[ARG_height].u_int;
    g_state.usd = args[ARG_usd].u_bool;
    g_state.mirror = args[ARG_mirror].u_bool;
    g_state.color_invert = args[ARG_color_invert].u_bool;
    g_state.baudrate = (uint32_t)args[ARG_baudrate].u_int;

    _INFO("setting initial pin states");
    mp_hal_pin_write(g_state.cs, 1);
    mp_hal_pin_write(g_state.dc, 0);
    mp_hal_pin_write(g_state.rst, 1);
    if (g_state.has_nfc_cs) {
        mp_hal_pin_write(g_state.nfc_cs, 1);
    }

    // Initialize the panel hardware.
    _INFO("starting panel init sequence");
    _init_sequence();

    // Color invert if requested.
    if (g_state.color_invert) {
        _write_cmd(0x21);  // Display inversion on
    }

    uint32_t buffer_lines = (uint32_t)args[ARG_buffer_lines].u_int;

    // Allocate draw buffer in RGB565 (2 bytes per pixel).
    size_t buf_pixels = (size_t)g_state.width * buffer_lines;
    size_t buf_bytes = buf_pixels * 2;
    uint8_t *buf1 = m_new(uint8_t, buf_bytes);
    if (buf1 == NULL) {
        mp_raise_OSError(MP_ENOMEM);
    }

    // Allocate RGB666 line buffer (worst case: full width).
    size_t linebuf_bytes = (size_t)g_state.width * 3;
    g_state.linebuf = m_new(uint8_t, linebuf_bytes);
    if (g_state.linebuf == NULL) {
        mp_raise_OSError(MP_ENOMEM);
    }

    // Create LVGL v9 display.
    _INFO("creating lvgl display");
    g_state.disp = lv_display_create(g_state.width, g_state.height);
    lv_display_set_flush_cb(g_state.disp, ili9488_flush_cb);
    lv_display_set_buffers(g_state.disp, buf1, NULL, buf_bytes, LV_DISPLAY_RENDER_MODE_PARTIAL);
    _INFO("display registration complete");

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(ili9488_lvgl_init_obj, 0, ili9488_lvgl_init);

static const mp_rom_map_elem_t ili9488_lvgl_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_ili9488_lvgl) },
    { MP_ROM_QSTR(MP_QSTR_init),       MP_ROM_PTR(&ili9488_lvgl_init_obj) },
};
static MP_DEFINE_CONST_DICT(ili9488_lvgl_globals, ili9488_lvgl_globals_table);

const mp_obj_module_t ili9488_lvgl_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&ili9488_lvgl_globals,
};

MP_REGISTER_MODULE(MP_QSTR_ili9488_lvgl, ili9488_lvgl_module);
