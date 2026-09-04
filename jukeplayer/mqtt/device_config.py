# jukeplayer/mqtt/device_config.py
# MQTT Home Assistant sensor definitions.
# This mirrors device_config.json but is frozen-firmware friendly.

SENSORS = [
    {
        "component": "sensor",
        "label": "Artist",
        "object_id": "artist",
        "extra_conf": {
            "unique_id": "{device_id}_artist",
            "value_template": "{{ value_json.artist }}",
            "icon": "mdi:account-music",
        },
    },
    {
        "component": "sensor",
        "label": "Album",
        "object_id": "album",
        "extra_conf": {
            "unique_id": "{device_id}_album",
            "value_template": "{{ value_json.album }}",
            "icon": "mdi:album",
        },
    },
    {
        "component": "sensor",
        "label": "Track",
        "object_id": "track",
        "extra_conf": {
            "unique_id": "{device_id}_track",
            "value_template": "{{ value_json.track }}",
            "icon": "mdi:music-note",
        },
    },
    {
        "component": "sensor",
        "label": "Player Status",
        "object_id": "player_status",
        "extra_conf": {
            "unique_id": "{device_id}_player_status",
            "value_template": "{{ value_json.player_status }}",
            "icon": "mdi:play-circle",
            "device_class": "enum",
            "options": ["PLAY", "PAUSE", "STOP"],
        },
    },
    {
        "component": "sensor",
        "label": "Volume",
        "object_id": "volume",
        "extra_conf": {
            "unique_id": "{device_id}_volume",
            "value_template": "{{ value_json.volume | int(0) }}",
            "icon": "mdi:volume-high",
            "unit_of_measurement": "%",
            "state_class": "measurement",
        },
    },
    {
        "component": "sensor",
        "label": "Network Status",
        "object_id": "network_status",
        "extra_conf": {
            "unique_id": "{device_id}_network_status",
            "value_template": "{{ value_json.network_status }}",
            "icon": "mdi:wifi",
            "device_class": "enum",
            "options": ["ws_connecting", "ws_connected", "ws_error", "wifi_disconnected", "unknown"],
        },
    },
    {
        "component": "binary_sensor",
        "label": "Repeat Status",
        "object_id": "repeat_status",
        "extra_conf": {
            "unique_id": "{device_id}_repeat_status",
            "value_template": "{{ 'ON' if value_json.repeat_status else 'OFF' }}",
            "icon": "mdi:repeat",
            "payload_on": "ON",
            "payload_off": "OFF",
        },
    },
    {
        "component": "binary_sensor",
        "label": "Mute Status",
        "object_id": "mute_status",
        "extra_conf": {
            "unique_id": "{device_id}_mute_status",
            "value_template": "{{ 'ON' if value_json.mute_status else 'OFF' }}",
            "icon": "mdi:volume-mute",
            "payload_on": "ON",
            "payload_off": "OFF",
        },
    },
    {
        "component": "sensor",
        "label": "Client ID",
        "object_id": "client_id",
        "extra_conf": {
            "unique_id": "{device_id}_client_id",
            "value_template": "{{ value_json.client_id }}",
            "icon": "mdi:identifier",
        },
    },
    {
        "component": "sensor",
        "label": "Memory Usage",
        "object_id": "memory_usage",
        "extra_conf": {
            "unique_id": "{device_id}_memory_usage",
            "value_template": "{{ value_json.memory_usage | int(0) }}",
            "icon": "mdi:memory",
            "unit_of_measurement": "%",
            "state_class": "measurement",
        },
    },
    {
        "component": "sensor",
        "label": "Last NFC Scan",
        "object_id": "last_nfc_scan",
        "extra_conf": {
            "unique_id": "{device_id}_last_nfc_scan",
            "value_template": "{{ value_json.last_nfc_scan }}",
            "icon": "mdi:nfc",
        },
    },
]
