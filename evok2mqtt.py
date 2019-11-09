#!/usr/bin/env python
import argparse
import asyncio
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


def _mqtt_client():
    """Singleton MQTT client"""
    global _MQTT_CLIENT
    if _MQTT_CLIENT is None:
        _MQTT_CLIENT = mqtt.Client()
    return _MQTT_CLIENT


def _read_config(filename):
    logging.info("Reading input config from %s", filename)
    with open(filename, "r") as fh:
        return yaml.safe_load(fh)


def _autodiscover_mqtt(inputs):
    """Push MQTT autodiscovery settings to MQTT broker"""
    for i in inputs:
        _mqtt_client().publish(
            MQTT_HASS_CONFIG_TOPIC_FORMAT.format(topic_suffix=i["hass_topic_suffix"]),
            payload=json.dumps(
                {
                    "name": i["hass_name"],
                    "command_topic": MQTT_HASS_COMMAND_TOPIC_FORMAT.format(
                        topic_suffix=["hass_topic_suffix"]
                    ),
                }
            ),
        )


async def process(payload):
    obj = json.loads(payload)[0]
    logging.info(obj)


async def ws_connect(evok_uri):
    logging.info("Connecting to %s", evok_uri)
    async with websockets.connect(evok_uri) as websocket:
        while True:
            payload = await websocket.recv()
            await (process(payload))


def _parser():
    """Generate argument parser"""
    parser = argparse.ArgumentParser()
    parser.add_argument("evok_uri", help="unipi websocket URI")
    parser.add_argument("mqtt_host", help="MQTT broker host")
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_timeout", type=int, default=60)
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

    # Push autodiscovery
    inputs = _read_config(args.config_file)
    _autodiscover_mqtt(inputs)

    # Loop
    asyncio.get_event_loop().run_until_complete(ws_connect(args.evok_uri))


if __name__ == "__main__":
    sys.exit(main())
