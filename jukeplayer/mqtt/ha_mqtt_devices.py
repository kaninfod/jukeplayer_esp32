from jukeplayer.core.state_constants import *
import time
from jukeplayer.mqtt.ha_mqtt_lib import EntityGroup
from jukeplayer.core.logger import log


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
        
        self._connected = False

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

    def _expand_placeholders(self, value):
        """Recursively expand placeholders in loaded device config values."""
        if isinstance(value, str):
            return (
                value.replace("{device_id}", str(self.device_id))
                .replace("{client_name}", str(self.client_name))
            )
        if isinstance(value, list):
            return [self._expand_placeholders(item) for item in value]
        if isinstance(value, dict):
            expanded = {}
            for key, item in value.items():
                expanded[key] = self._expand_placeholders(item)
            return expanded
        return value

    def _sensor_definitions(self):
        """Load sensor definitions from JSON config file."""
        try:
            import ujson as json
        except ImportError:
            import json

        config_path = "jukeplayer/mqtt/device_config.json"
        with open(config_path, "r") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            definitions = loaded.get("sensors", [])
        else:
            definitions = loaded

        return self._expand_placeholders(definitions)

    def _initialize_discovery(self):
        node_id = self.client_name.replace(" ", "_").lower().encode("utf-8") # self.device_id.encode("utf-8") if isinstance(self.device_id, str) else self.device_id
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
            if not isinstance(item, dict):
                self.logger.info(f"[MQTT] Skipping invalid sensor config entry: {item}")
                continue

            label = item.get("label")
            object_id = item.get("object_id")
            if not label or object_id is None:
                self.logger.info(f"[MQTT] Skipping incomplete sensor config entry: {item}")
                continue

            if isinstance(object_id, str):
                object_id = object_id.encode("utf-8")

            extra_conf = item.get("extra_conf", {})
            if not isinstance(extra_conf, dict):
                extra_conf = {}

            component = item.get("component", "sensor")

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

    async def run(self):
        import asyncio
        import gc
        if not self.enabled:
            return

        reconnect_delay = 2
        max_reconnect_delay = 60
        
        while True:
            try:
                gc.collect()
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
                self._connected = True
                reconnect_delay = 2
                
                self._initialize_discovery()
                self.publish_snapshot(reason="initial")
                
                # Check link health every second instead of sleeping for two minutes straight
                periodic_timer = 0
                ping_timer = 0

                while self._connected:
                    await asyncio.sleep(1)
                    periodic_timer += 1
                    ping_timer += 1

                    if ping_timer >= 25:
                        ping_timer = 0
                        try:
                            # Send a lightweight MQTT keep-alive ping packet
                            self.mqtt_client.ping()
                        except Exception as e:
                            self.logger.error(f"[MQTT] Keep-alive ping failed: {e}")
                            self._connected = False
                            break
                    
                    if periodic_timer >= 120:
                        periodic_timer = 0
                        self.publish_snapshot(reason="periodic")
                    
            except Exception as e:
                self.logger.error(f"[MQTT] Connection/Loop error: {e}")
                
            self._connected = False
            self.cleanup()
            self.logger.info(f"[MQTT] Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    def publish_snapshot(self, reason="update", state: dict = None):
        """Publish app state maps via MQTT if connected."""
        if not (self.enabled and self.entity_group):
            return False
            
        try:
            # Never mutate shared AppState directly here; use a local payload dict.
            payload = state.copy() if state else self._build_snapshot()
            
            # Inject a fresh calendar time stamp cleanly
            t = time.localtime()
            payload["timestamp"] = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                t[0], t[1], t[2], t[3], t[4], t[5]
            )
            
            self.logger.info(f"[MQTT] Publishing snapshot ({reason})") #: {payload}
            self.entity_group.publish_state(payload)
            return True
            
        except Exception as e:
            self.logger.error(f"[MQTT] Failed to publish snapshot: {e}")
            self._connected = False
            return False


    # def publish_snapshot(self, reason="update", state: dict = {}):
    #     """Publish current app state via MQTT if connected."""
    #     if self.enabled and self.entity_group:
    #         try:
    #             self.logger.info(f"[MQTT] Publishing snapshot. State: {state}, reason: {reason}")
    #             payload = self._build_snapshot(state=state)
    #             self.logger.info(f"[MQTT] Publishing snapshot with payload: {payload}")
    #             self.entity_group.publish_state(payload)
    #             self.logger.info(f"[MQTT] Snapshot published ({reason})")
    #             return True
    #         except Exception as e:
    #             self.logger.error(f"[MQTT] Failed to publish snapshot: {e}")
    #             return False
    #     return False

    def _build_snapshot(self, state: dict = {}):
        """Build a normalized MQTT payload from current app state."""
        now = time.localtime()
        timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            now[0], now[1], now[2], now[3], now[4], now[5]
        )

        if state not in (None, {}):
            mqtt_state = {}
            for key, value in state.items():
                mqtt_state[key] = value
            mqtt_state["timestamp"] = timestamp
            self.logger.info(f"[MQTT] Built snapshot from provided state: {mqtt_state}")
            return mqtt_state
        else:
            app_state = self.app.state.data

            return {
                "artist": app_state[ARTIST],
                "album": app_state[ALBUM],
                "track": app_state[TRACK],
                "player_status": app_state[PLAYER_STATUS],
                "volume": app_state[VOLUME],
                "network_status": app_state[NETWORK_STATUS],
                "repeat_status": app_state[REPEAT_STATUS],
                "mute_status": app_state[MUTED],
                "client_id": app_state[CLIENT_ID],
                "memory_usage": app_state[MEMORY_USAGE],
                "timestamp": timestamp,
                "last_nfc_scan": app_state[LAST_NFC_SCAN],
            }

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