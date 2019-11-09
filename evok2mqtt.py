#!/usr/bin/env python
import argparse
import asyncio
import hashlib
import json
import logging
import logging.config
import sys

import yaml

import paho.mqtt.client as mqtt
import websockets

# URI = "ws://tesla.lan/ws"

# Log setup
LOG_LEVEL = logging.INFO
LOG_CONFIG = dict(
    version=1,
    formatters={
        "default": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"}
    },
    handlers={
        "stream": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": LOG_LEVEL,
        }
    },
    root={"handlers": ["stream"], "level": LOG_LEVEL},
)

logging.config.dictConfig(LOG_CONFIG)
logger = logging.getLogger(__name__)

# MQTT
_MQTT_CLIENT = None
MQTT_HASS_CONFIG_TOPIC_FORMAT = "homeassistant/switch/push_button_{topic_suffix}/config"
MQTT_HASS_COMMAND_TOPIC_FORMAT = "homeassistant/switch/push_button_{topic_suffix}/set"
MQTT_HASS_STATE_TOPIC_FORMAT = "homeassistant/switch/push_button_{topic_suffix}/state"


# Config
class Config:
    """Representation of setting, checksummed, for easy comparison"""

    def __init__(self, hass_name, hass_topic_suffix, unipi_dev, unipi_circuit):
        self.hass_name = hass_name
        self.hass_topic_suffix = hass_topic_suffix
        self.unipi_dev = unipi_dev
        self.unipi_circuit = unipi_circuit
        self._checksum = self._calculate_checksum()

    def _calculate_checksum(self):
        m = hashlib.sha256()
        for field in (
            self.hass_name,
            self.hass_topic_suffix,
            self.unipi_dev,
            self.unipi_circuit,
        ):
            m.update(str(field).encode())
        return m.digest()

    def __eq__(self, other):
        return self._checksum == other._checksum

    def unipi_match(self, unipi_dev, unipi_circuit):
        """Check whether incoming websocket hook matches config"""
        return self.unipi_dev == unipi_dev and self.unipi_circuit == unipi_circuit

    def __repr__(self):
        return f"<Config {self.hass_name} -- {self.unipi_circuit}>"


def _read_config(filename):
    logger.info("Reading input config from %s", filename)
    with open(filename, "r") as fh:
        return [Config(**line) for line in yaml.safe_load(fh)]


# MQTT
def _mqtt_client():
    """Singleton MQTT client"""
    global _MQTT_CLIENT
    if _MQTT_CLIENT is None:
        _MQTT_CLIENT = mqtt.Client()
    return _MQTT_CLIENT


def _autodiscover_mqtt(configs):
    """Push MQTT autodiscovery settings to MQTT broker"""
    for config in configs:
        _mqtt_client().publish(
            MQTT_HASS_CONFIG_TOPIC_FORMAT.format(topic_suffix=config.hass_topic_suffix),
            payload=json.dumps(
                {
                    "name": config.hass_name,
                    "command_topic": MQTT_HASS_COMMAND_TOPIC_FORMAT.format(
                        topic_suffix=config.hass_topic_suffix
                    ),
                    "state_topic": MQTT_HASS_STATE_TOPIC_FORMAT.format(
                        topic_suffix=config.hass_topic_suffix
                    ),
                }
            ),
        )


# Websockets
async def _ws_process(payload, configs, mqtt_payload_on, mqtt_payload_off):
    obj = json.loads(payload)[0]
    logger.debug("Incoming message for websocket %s", obj)
    for config in configs:
        if config.unipi_match(obj["dev"], obj["circuit"]):
            logger.info("Matching config for %s and message %s", config, obj)
            topic = MQTT_HASS_STATE_TOPIC_FORMAT.format(
                topic_suffix=config.hass_topic_suffix
            )
            payload = mqtt_payload_on if obj["value"] == 1 else mqtt_payload_off
            _mqtt_client().publish(topic, payload=payload)
            logger.info("MQTT publish %s to topic %s", payload, topic)
            return


async def ws_connect(evok_uri, configs, mqtt_payload_on, mqtt_payload_off):
    logger.info("Connecting to %s", evok_uri)
    async with websockets.connect(evok_uri) as websocket:
        while True:
            payload = await websocket.recv()
            await _ws_process(payload, configs, mqtt_payload_on, mqtt_payload_off)


def _parser():
    """Generate argument parser"""
    parser = argparse.ArgumentParser()
    parser.add_argument("evok_uri", help="unipi websocket URI")
    parser.add_argument("mqtt_host", help="MQTT broker host")
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_timeout", type=int, default=60)
    parser.add_argument("--mqtt_payload_on", default=b"ON")
    parser.add_argument("--mqtt_payload_off", default=b"OFF")
    parser.add_argument(
        "--config_file", help="Configuration file", default="config.yaml"
    )
    return parser


def main():
    # Parse
    parser = _parser()
    args = parser.parse_args()

    # MQTT initial setup
    logger.info("Connecting to MQTT broker %s", args.mqtt_host)
    _mqtt_client().connect(args.mqtt_host, args.mqtt_port, args.mqtt_timeout)

    # Read configs
    configs = _read_config(args.config_file)
    # _set_settings(configs, args.mqtt_payload_on, args.mqtt_payload_off)

    # Push autodiscovery
    _autodiscover_mqtt(configs)

    # Loop
    _mqtt_client().loop_start()
    asyncio.get_event_loop().run_until_complete(
        ws_connect(args.evok_uri, configs, args.mqtt_payload_on, args.mqtt_payload_off)
    )


if __name__ == "__main__":
    sys.exit(main())
