// Minimal ILI9488 display driver for LVGL v9 on MicroPython / ESP32.
//
// The SPI bus must already be configured at the desired display speed before
// ili9488_lvgl.init() is called. This driver does NOT call back into Python.
// It only handles low-level pin toggling, ILI9488 init commands, and the LVGL
// flush callback.

#include "py/runtime.h"
#include "py/stream.h"
#include "py/mphal.h"
#include "py/obj.h"
#include "extmod/modmachine.h"

#include "lvgl.h"

extern mp_obj_t mp_pin_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args);

// Debug helper (kept minimal).
#define _INFO(fmt, ...)  mp_printf(&mp_plat_print, "[ILI9488] " fmt "\n", ##__VA_ARGS__)

// ---------------------------------------------------------------------------
// Driver state
// ---------------------------------------------------------------------------

typedef struct {
    mp_obj_t spi;                       // machine.SPI object
    mp_hal_pin_obj_t cs;              // chip-select
    mp_hal_pin_obj_t dc;              // data/command
    mp_hal_pin_obj_t rst;             // reset
    mp_hal_pin_obj_t nfc_cs;          // NFC chip-select (kept high during display ops)
    bool has_nfc_cs;
    int32_t width;
    int32_t height;
    bool usd;
    bool mirror;
    bool color_invert;

    lv_display_t *disp;
    uint8_t *linebuf;                 // Temporary RGB666 line buffer
} ili9488_state_t;

static ili9488_state_t g_state;

// ---------------------------------------------------------------------------
// Low-level SPI helpers
// ---------------------------------------------------------------------------

static inline void _cs_low(void) {
    mp_hal_pin_write(g_state.cs, 0);
}

static inline void _cs_high(void) {
    mp_hal_pin_write(g_state.cs, 1);
}

static inline void _dc_cmd(void) {
    mp_hal_pin_write(g_state.dc, 0);
}

static inline void _dc_data(void) {
    mp_hal_pin_write(g_state.dc, 1);
}

static int _spi_write(const void *buf, size_t len) {
    mp_obj_base_t *s = (mp_obj_base_t *)MP_OBJ_TO_PTR(g_state.spi);
    mp_machine_spi_p_t *spi_p = (mp_machine_spi_p_t *)MP_OBJ_TYPE_GET_SLOT(s->type, protocol);
    spi_p->transfer(s, len, (const uint8_t *)buf, NULL);
    return 0;
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

// ---------------------------------------------------------------------------
// ILI9488 initialization and window setup
// ---------------------------------------------------------------------------

static void _hw_reset(void) {
    mp_hal_pin_write(g_state.rst, 0);
    _sleep_ms(50);
    mp_hal_pin_write(g_state.rst, 1);
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
    _hw_reset();

    _write_cmd(0x01);  // SWRESET
    _sleep_ms(100);

    _write_cmd(0x11);  // Sleep out
    _sleep_ms(20);

    _write_cmd(0x3A);  // Interface pixel format
    {
        uint8_t fmt = 0x66;  // 18 bits / pixel
        _write_data(&fmt, 1);
    }

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

    uint8_t madctl;
    if (g_state.width > g_state.height) {
        madctl = g_state.usd ? 0xE8 : 0x28;
    } else {
        madctl = g_state.usd ? 0x48 : 0x88;
    }
    if (g_state.mirror) {
        madctl ^= 0x80;
    }
    _write_cmd(0x36);
    _write_data(&madctl, 1);

    _write_cmd(0x11);  // Sleep out
    _write_cmd(0x29);  // Display on
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

    if (g_state.has_nfc_cs) {
        mp_hal_pin_write(g_state.nfc_cs, 1);
    }

    _set_addr_window(x1, y1, area->x2, area->y2);
    _write_cmd(0x2C);  // Memory write

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
        ARG_nfc_cs, ARG_buffer_lines
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

    _INFO("init start");

    mp_hal_pin_write(g_state.cs, 1);
    mp_hal_pin_write(g_state.dc, 0);
    mp_hal_pin_write(g_state.rst, 1);
    if (g_state.has_nfc_cs) {
        mp_hal_pin_write(g_state.nfc_cs, 1);
    }

    _init_sequence();

    if (g_state.color_invert) {
        _write_cmd(0x21);
    }

    uint32_t buffer_lines = (uint32_t)args[ARG_buffer_lines].u_int;
    _INFO("allocating buffers width=%ld buffer_lines=%lu",
          (long)g_state.width, (unsigned long)buffer_lines);
    if (buffer_lines == 0 || buffer_lines > g_state.height) {
        buffer_lines = 40;
    }
    size_t buf_pixels = (size_t)g_state.width * (size_t)buffer_lines;
    size_t buf_bytes = buf_pixels * 2;
    _INFO("buf_pixels=%lu buf_bytes=%lu", (unsigned long)buf_pixels, (unsigned long)buf_bytes);
    uint8_t *buf1 = m_malloc(buf_bytes);
    if (buf1 == NULL) {
        mp_raise_OSError(MP_ENOMEM);
    }

    size_t linebuf_bytes = (size_t)g_state.width * 3;
    _INFO("linebuf_bytes=%lu", (unsigned long)linebuf_bytes);
    g_state.linebuf = m_malloc(linebuf_bytes);
    if (g_state.linebuf == NULL) {
        mp_raise_OSError(MP_ENOMEM);
    }

    _INFO("buf1=%p linebuf=%p", (void *)buf1, (void *)g_state.linebuf);

    // Create LVGL v9 display.
    _INFO("creating display %ldx%ld", (long)g_state.width, (long)g_state.height);
    g_state.disp = lv_display_create(g_state.width, g_state.height);
    _INFO("display created disp=%p", (void *)g_state.disp);
    lv_display_set_flush_cb(g_state.disp, ili9488_flush_cb);
    _INFO("flush cb set");
    lv_display_set_buffers(g_state.disp, buf1, NULL, buf_bytes, LV_DISPLAY_RENDER_MODE_PARTIAL);
    _INFO("buffers set");

    _INFO("init done");
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
