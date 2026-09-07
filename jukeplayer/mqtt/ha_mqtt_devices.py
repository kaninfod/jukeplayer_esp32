from jukeplayer.core.state_constants import *
import time
import asyncio
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
        self.node_id = self.client_name.replace(" ", "_").lower()
        # Retained LWT topic: HA marks all entities unavailable when the device
        # or its MQTT connection drops
        self.availability_topic = b"jukeplayer/" + self.node_id.encode("utf-8") + b"/availability"

        # Debounced publish state for rapid AppState deltas.
        self._pending_state = {}
        self._publish_task = None
        self._publish_debounce_ms = 250

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
        """Load sensor definitions from frozen Python config or JSON file."""
        try:
            import ujson as json
        except ImportError:
            import json

        # 1. Prefer frozen-firmware friendly Python module.
        try:
            from jukeplayer.mqtt import device_config
            definitions = getattr(device_config, "SENSORS", [])
            if isinstance(definitions, list):
                self.logger.info("[MQTT] loaded sensor definitions from device_config.py")
                return self._expand_placeholders(definitions)
        except Exception as e:
            self.logger.info(f"[MQTT] device_config.py not available: {e}")

        # 2. Fall back to JSON file for filesystem-based deployments.
        config_path = "jukeplayer/mqtt/device_config.json"
        try:
            with open(config_path, "r") as f:
                loaded = json.load(f)

            if isinstance(loaded, dict):
                definitions = loaded.get("sensors", [])
            else:
                definitions = loaded

            self.logger.info("[MQTT] loaded sensor definitions from device_config.json")
            return self._expand_placeholders(definitions)
        except OSError as e:
            self.logger.warning(f"[MQTT] {config_path} not found: {e}. Discovery disabled.")
            return []
        except Exception as e:
            self.logger.error(f"[MQTT] failed to parse {config_path}: {e}. Discovery disabled.")
            return []

    def _initialize_discovery(self):
        node_id = self.node_id.encode("utf-8")
        device_config = {
            "identifiers": [self.device_id],
            "name": self.client_name,
            "model": "JukePlayer ESP32",
            "manufacturer": "JukePlayer Team",
            "sw_version": "1.0.0",
        }
        # Availability propagates to every entity in the group via
        # EntityGroup._update_extra_conf
        group_conf = {
            "device": device_config,
            "availability_topic": self.availability_topic.decode("utf-8"),
            "payload_available": "online",
            "payload_not_available": "offline",
        }

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

                # Async reachability pre-check: umqtt's connect() is a blocking
                # socket call with no timeout; probing the broker cooperatively
                # first keeps an unreachable broker from stalling the whole
                # event loop for tens of seconds on every reconnect attempt.
                try:
                    _probe_reader, _probe_writer = await asyncio.wait_for(
                        asyncio.open_connection(self.broker, self.port), 5
                    )
                    _probe_writer.close()
                    await _probe_writer.wait_closed()
                except Exception as probe_err:
                    self.logger.info(f"[MQTT] Broker unreachable: {probe_err}")
                    raise  # fall through to the shared backoff path

                self.mqtt_client = MQTTClient(
                    client_id=client_id,
                    server=self.broker,
                    port=self.port,
                    user=self.user if self.user else None,
                    password=self.password if self.password else None,
                    keepalive=60
                )
                # Last will: the broker flips every entity to "unavailable" if
                # the connection drops without a clean disconnect
                self.mqtt_client.set_last_will(self.availability_topic, b"offline", True, 1)

                self.mqtt_client.connect()
                self.logger.info("[MQTT] Connected successfully!")
                self._connected = True
                reconnect_delay = 2
                self.mqtt_client.publish(self.availability_topic, b"online", True, 1)
                
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

    def publish_snapshot(self, state: dict = None, reason="update"):
        """Publish app state maps via MQTT if connected.

        Rapid state updates are debounced so multiple deltas in quick
        succession collapse into a single publish.
        """
        if not self.enabled:
            return False

        # Merge incoming delta into pending state so rapid changes do not
        # overwrite each other (e.g. player_status must survive until publish).
        if state:
            for key, value in state.items():
                self._pending_state[key] = value

        if self._publish_task and not self._publish_task.done():
            return True  # Already scheduled; latest merged state will be picked up.

        self._publish_task = asyncio.create_task(self._run_publish(reason))
        return True

    async def _run_publish(self, reason):
        """Wait briefly, then publish the latest merged state."""
        try:
            await asyncio.sleep_ms(self._publish_debounce_ms)
            state = self._pending_state
            self._pending_state = {}

            if not (self.enabled and self.entity_group):
                return False

            # Normalize to a payload with string keys for Home Assistant templates.
            payload = self._build_snapshot(state)
            

            # Inject a fresh calendar time stamp cleanly
            t = time.localtime()
            payload["timestamp"] = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                t[0], t[1], t[2], t[3], t[4], t[5]
            )

            self.logger.info(f"[MQTT] Publishing snapshot ({reason})")
            self.entity_group.publish_state(payload)
            return True

        except OSError as e:
            # Connection-level error: let the main loop reconnect.
            self.logger.error(f"[MQTT] Publish failed (connection): {e}")
            self._connected = False
            return False
        except Exception as e:
            # Transient publish error: log but stay connected.
            self.logger.error(f"[MQTT] Failed to publish snapshot: {e}")
            return False
        finally:
            self._publish_task = None


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

    def _build_snapshot(self, state: dict = None):
        """Build a normalized MQTT payload from current app state.

        The result always uses string keys so Home Assistant value_template
        references like {{ value_json.artist }} work regardless of whether the
        input is a full state dict or an AppState delta with integer keys.
        """
        now = time.localtime()
        timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            now[0], now[1], now[2], now[3], now[4], now[5]
        )

        # Map AppState integer constants to MQTT payload string keys.
        key_map = {
            ARTIST: "artist",
            ALBUM: "album",
            TRACK: "track",
            PLAYER_STATUS: "player_status",
            VOLUME: "volume",
            NETWORK_STATUS: "network_status",
            REPEAT_STATUS: "repeat_status",
            MUTED: "mute_status",
            CLIENT_ID: "client_id",
            MEMORY_USAGE: "memory_usage",
            LAST_NFC_SCAN: "last_nfc_scan",
        }

        mqtt_state = {"timestamp": timestamp}

        source = state if state is not None else self.app.state.data
        for key, value in source.items():
            name = key_map.get(key, key)
            mqtt_state[name] = value

        # Ensure every known key has a value even if absent from the delta.
        app_state = self.app.state.data
        for const, name in key_map.items():
            if name not in mqtt_state:
                mqtt_state[name] = app_state.get(const, "")

        return mqtt_state

    def publish_state(self, _text):
        """Backward-compatible alias for older call sites."""
        self.publish_snapshot(reason="state_alias")

    def cleanup(self):
        """Clean up MQTT client connections."""
        if self.mqtt_client:
            try:
                # Clean disconnects don't trigger the LWT — mark offline explicitly
                self.mqtt_client.publish(self.availability_topic, b"offline", True, 1)
            except Exception:
                pass
            try:
                self.mqtt_client.disconnect()
            except Exception:
                pass
            self.mqtt_client = None
        self.entity_group = None
        self.entities = []