import time
from jukeplayer.mqtt.ha_mqtt_lib import EntityGroup

try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None

class HAMQTTService:
    def __init__(self, app):
        """
        Service to manage the MQTT connection, keep-alive loop, and publishing telemetry.
        """
        self.app = app
        self.logger = app.logger
        self.config = app.config
        
        self.mqtt_cfg = self.config.get("mqtt", {})
        self.enabled = self.mqtt_cfg.get("enabled", False) and MQTTClient is not None
        
        self.mqtt_client = None
        self.entity_group = None
        self.entities = []
        
        self.broker = self.mqtt_cfg.get("broker")
        self.port = self.mqtt_cfg.get("port", 1883)
        self.user = self.mqtt_cfg.get("user", "")
        self.password = self.mqtt_cfg.get("password", "")
        
        self.client_name = self.config.get("client", {}).get("name", "JukePlayer")
        self.device_id = self.config.get("client", {}).get("device_id", "jukeplayer_esp32")

    def _sensor_definitions(self):
        return [
            {
                "component": "sensor",
                "label": "Artist",
                "object_id": b"artist",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_artist",
                    "value_template": "{{ value_json.artist }}",
                    "icon": "mdi:account-music",
                },
            },
            {
                "component": "sensor",
                "label": "Album",
                "object_id": b"album",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_album",
                    "value_template": "{{ value_json.album }}",
                    "icon": "mdi:album",
                },
            },
            {
                "component": "sensor",
                "label": "Track",
                "object_id": b"track",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_track",
                    "value_template": "{{ value_json.track }}",
                    "icon": "mdi:music-note",
                },
            },
            {
                "component": "sensor",
                "label": "Player Status",
                "object_id": b"player_status",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_player_status",
                    "value_template": "{{ value_json.player_status }}",
                    "icon": "mdi:play-circle",
                    "device_class": "enum",
                    "options": ["playing", "paused", "stopped", "idle"],
                },
            },
            {
                "component": "sensor",
                "label": "Volume",
                "object_id": b"volume",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_volume",
                    "value_template": "{{ value_json.volume | int(0) }}",
                    "icon": "mdi:volume-high",
                    "unit_of_measurement": "%",
                    "state_class": "measurement",
                },
            },
            {
                "component": "sensor",
                "label": "Network Status",
                "object_id": b"network_status",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_network_status",
                    "value_template": "{{ value_json.network_status }}",
                    "icon": "mdi:wifi",
                    "device_class": "enum",
                    "options": ["ws_connecting", "ws_connected", "ws_error", "wifi_disconnected", "unknown"],
                },
            },
            {
                "component": "binary_sensor",
                "label": "Repeat Status",
                "object_id": b"repeat_status",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_repeat_status",
                    "value_template": "{{ 'ON' if value_json.repeat_status else 'OFF' }}",
                    "icon": "mdi:repeat",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                },
            },
            {
                "component": "binary_sensor",
                "label": "Mute Status",
                "object_id": b"mute_status",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_mute_status",
                    "value_template": "{{ 'ON' if value_json.mute_status else 'OFF' }}",
                    "icon": "mdi:volume-mute",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                },
            },
            {
                "component": "sensor",
                "label": "Client ID",
                "object_id": b"client_id",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_client_id",
                    "value_template": "{{ value_json.client_id }}",
                    "icon": "mdi:identifier",
                },
            },
            {
                "component": "sensor",
                "label": "Memory Usage",
                "object_id": b"memory_usage",
                "extra_conf": {
                    "unique_id": f"{self.device_id}_memory_usage",
                    "value_template": "{{ value_json.memory_usage | int(0) }}",
                    "icon": "mdi:memory",
                    "unit_of_measurement": "%",
                    "state_class": "measurement",
                },
            },
        ]

    def _initialize_discovery(self):
        node_id = self.device_id.encode("utf-8") if isinstance(self.device_id, str) else self.device_id
        device_config = {
            "identifiers": [self.device_id],
            "name": self.client_name,
            "model": "JukePlayer ESP32",
            "manufacturer": "JukePlayer Team",
            "sw_version": "1.0.0",
        }
        group_conf = {"device": device_config}

        self.entity_group = EntityGroup(self.mqtt_client, node_id=node_id, extra_conf=group_conf)
        self.entities = []

        for item in self._sensor_definitions():
            label = item["label"]
            object_id = item["object_id"]
            extra_conf = item["extra_conf"]
            component = item["component"]

            if component == "binary_sensor":
                entity = self.entity_group.create_binary_sensor(
                    name=f"{self.client_name} {label}",
                    object_id=object_id,
                    extra_conf=extra_conf,
                )
            else:
                entity = self.entity_group.create_sensor(
                    name=f"{self.client_name} {label}",
                    object_id=object_id,
                    extra_conf=extra_conf,
                )
            self.entities.append(entity)

    def _build_snapshot(self):
        """Build a normalized MQTT payload from current app state."""
        app_state = self.app.state
        now = time.localtime()
        timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            now[0], now[1], now[2], now[3], now[4], now[5]
        )

        return {
            "artist": app_state.get("artist", ""),
            "album": app_state.get("album", ""),
            "track": app_state.get("track", ""),
            "player_status": app_state.get("player_status", "idle"),
            "volume": app_state.get("volume", 0),
            "network_status": app_state.get("network_status", "unknown"),
            "repeat_status": app_state.get("repeat_status", False),
            "mute_status": app_state.get("mute_status", False),
            "client_id": app_state.get("client_id", ""),
            "memory_usage": app_state.get("memory_usage", 0),
            "timestamp": timestamp,
        }

    async def run(self):
        """Background loop to manage MQTT connection and publish periodic updates."""
        import asyncio
        
        if not self.enabled:
            if MQTTClient is None:
                self.logger.error("[MQTT] umqtt.simple not found. Disabling MQTT service.")
            else:
                self.logger.info("[MQTT] service is disabled in config.")
            return

        reconnect_delay = 2
        max_reconnect_delay = 60
        
        while True:
            try:
                self.logger.info(f"[MQTT] Connecting to broker {self.broker}:{self.port}...")
                
                client_id = f"jukeplayer_{self.device_id}"
                
                self.mqtt_client = MQTTClient(
                    client_id=client_id,
                    server=self.broker,
                    port=self.port,
                    user=self.user if self.user else None,
                    password=self.password if self.password else None,
                    keepalive=60
                )
                
                self.mqtt_client.connect()
                self.logger.info("[MQTT] Connected successfully!")
                reconnect_delay = 2
                
                self._initialize_discovery()
                self.logger.info("[MQTT] HA MQTT sensors initialized & discovery sent.")
                if not self.publish_snapshot(reason="initial"):
                    raise Exception("Initial MQTT publish failed")
                
                # Periodically publish updates while connected
                while True:
                    if not self.publish_snapshot(reason="periodic"):
                        self.logger.error("[MQTT] Publish failed, reconnecting...")
                        break
                    
                    await asyncio.sleep(30)
                    
            except Exception as e:
                self.logger.error(f"[MQTT] Connection/Loop error: {e}")
                
            self.cleanup()
            
            self.logger.info(f"[MQTT] Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    def publish_snapshot(self, reason="update"):
        """Publish current app state via MQTT if connected."""
        if self.enabled and self.entity_group:
            try:
                payload = self._build_snapshot()
                self.entity_group.publish_state(payload)
                self.logger.info(f"[MQTT] Snapshot published ({reason})")
                return True
            except Exception as e:
                self.logger.error(f"[MQTT] Failed to publish snapshot: {e}")
                return False
        return False

    def publish_state(self, _text):
        """Backward-compatible alias for older call sites."""
        self.publish_snapshot(reason="state_alias")

    def cleanup(self):
        """Clean up MQTT client connections."""
        if self.mqtt_client:
            try:
                self.mqtt_client.disconnect()
            except Exception:
                pass
            self.mqtt_client = None
        self.entity_group = None
        self.entities = []
