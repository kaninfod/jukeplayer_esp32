# CMake file for the ILI9488 LVGL user C module.
#
# This module pulls in lv_binding_micropython itself, so you only need to
# pass this single file to the MicroPython build via USER_C_MODULES:
#
#   make ... USER_C_MODULES=/project/jukeplayer/hardware/lvgl/micropython.cmake
#
# lv_binding_micropython is expected to live at:
#   micropython/ports/esp32/lv_binding_micropython
# relative to the build root mounted at /project in the Docker container.

# Allow overriding the binding location from the build command.
if (NOT DEFINED JUKEPLAYER_LVGL_BINDING_DIR)
    set(JUKEPLAYER_LVGL_BINDING_DIR /project/micropython/ports/esp32/lv_binding_micropython)
endif()

# Tell LVGL where to find lv_conf.h. This must be set before including the
# binding so that the LVGL component sees it during its own configuration.
if (NOT DEFINED LV_CONF_PATH)
    set(LV_CONF_PATH "${JUKEPLAYER_LVGL_BINDING_DIR}/lv_conf.h")
endif()

if (NOT EXISTS "${JUKEPLAYER_LVGL_BINDING_DIR}/micropython.cmake")
    message(FATAL_ERROR
        "lv_binding_micropython not found at ${JUKEPLAYER_LVGL_BINDING_DIR}. "
        "Make sure the submodule is initialized. "
        "See jukeplayer/hardware/lvgl/README.md for setup instructions.")
endif()

# Include lv_binding_micropython first so that LVGL targets and include
# directories are defined before this module is built.
include("${JUKEPLAYER_LVGL_BINDING_DIR}/micropython.cmake")

add_library(usermod_ili9488_lvgl INTERFACE)

target_sources(usermod_ili9488_lvgl INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/ili9488_driver.c
)

# The driver needs the same LVGL config path and include directories as the
# binding, and it needs to link against the binding target so that the `lv`
# module symbols are available.
target_compile_options(usermod_ili9488_lvgl INTERFACE
    -DLV_CONF_PATH="${LV_CONF_PATH}"
    -Wno-deprecated-declarations
)

target_include_directories(usermod_ili9488_lvgl INTERFACE ${LV_INCLUDE})

if (DEFINED LV_CONF_DIR)
    target_include_directories(usermod_ili9488_lvgl INTERFACE ${LV_CONF_DIR})
endif()

target_link_libraries(usermod_ili9488_lvgl INTERFACE usermod_lvgl)
target_link_libraries(usermod INTERFACE usermod_ili9488_lvgl)
