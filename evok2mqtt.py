#!/usr/bin/env python
import argparse
import asyncio
import collections
import json
import logging
import logging.config
import re
import sys

import paho.mqtt.client as mqtt
import websockets
import yaml

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


_SETTINGS = None

# Settings
Settings = collections.namedtuple(
    "Settings",
    (
        "UNIPI_NAME",
        "EVOK_URI",
        "MQTT_HOST",
        "MQTT_PORT",
        "MQTT_PAYLOAD_ON",
        "MQTT_PAYLOAD_OFF",
        "CONFIGS",
    ),
)


def _set_settings(
    unipi_name,
    evok_uri,
    mqtt_host,
    mqtt_port,
    mqtt_payload_on,
    mqtt_payload_off,
    configs,
):
    """
    Put all settings in global scope, as they don't change after intialization
    and make it easier to define callbacks
    """
    global _SETTINGS
    if _SETTINGS is not None:
        logger.warning("Updating settings")
    _SETTINGS = Settings(
        unipi_name,
        evok_uri,
        mqtt_host,
        mqtt_port,
        mqtt_payload_on,
        mqtt_payload_off,
        configs,
    )


def _settings():
    global _SETTINGS
    if _SETTINGS is None:
        logger.warning("Settings haven't been initialized, this will probably not work")
    return _SETTINGS


# Config
Config = collections.namedtuple(
    "Config", ("hass_name", "hass_type", "unipi_dev", "unipi_circuit")
)


def _read_config(filename):
    logger.info("Reading input config from %s", filename)
    with open(filename, "r") as fh:
        return [Config(**line) for line in yaml.safe_load(fh)]


def _config_maps(configs):
    """Build dictionaries for mapping return values to configs"""
    ws_to_mqtt = {}
    for config in configs:
        ws_to_mqtt[(config.unipi_dev, config.unipi_circuit)] = config
    return ws_to_mqtt


# Websockets
async def _ws_process(payload, config_map):
    """Process incoming websocket payload, push to MQTT"""
    obj = json.loads(payload)[0]
    logger.debug("Incoming message for websocket %s", obj)
    try:
        config = config_map[(obj["dev"], obj["circuit"])]
    except KeyError:
        return

    logger.info("Matching config for %s and message %s", config, obj)
    topic = MQTT_HASS_STATE_TOPIC_FORMAT.format(
        hass_type=config.hass_type,
        unipi_name=_settings().UNIPI_NAME,
        unipi_dev=config.unipi_dev,
        unipi_circuit=config.unipi_circuit,
    )
    payload = (
        _settings().MQTT_PAYLOAD_ON
        if obj["value"] == 1
        else _settings().MQTT_PAYLOAD_OFF
    )
    _mqtt_client().publish(topic, payload=payload)
    logger.info("MQTT publish %s to topic %s", payload, topic)


async def _ws_loop():
    """Main loop polling incoming events from websockets"""
    config_map = _config_maps(_settings().CONFIGS)
    logger.info("Connecting to %s", _settings().EVOK_URI)
    async with websockets.connect(_settings().EVOK_URI) as websocket:
        while True:
            payload = await websocket.recv()
            await _ws_process(payload, config_map)


async def _ws_trigger(unipi_dev, unipi_circuit, value):
    """Send MQTT message for config with given value"""
    async with websockets.connect(_settings().EVOK_URI) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "cmd": "set",
                    "dev": unipi_dev,
                    "circuit": unipi_circuit,
                    "value": value,
                }
            )
        )


# MQTT
_MQTT_CLIENT = None
MQTT_HASS_CONFIG_TOPIC_FORMAT = (
    "homeassistant/{hass_type}/{unipi_name}/{unipi_dev}_{unipi_circuit}/config"
)
MQTT_HASS_COMMAND_TOPIC_FORMAT = (
    "homeassistant/{hass_type}/{unipi_name}/{unipi_dev}_{unipi_circuit}/set"
)
MQTT_HASS_STATE_TOPIC_FORMAT = (
    "homeassistant/{hass_type}/{unipi_name}/{unipi_dev}_{unipi_circuit}/state"
)
MQTT_HASS_COMAND_TOPIC_REGEX = re.compile(
    r"^homeassistant/(?P<hass_type>(\w+))/(?P<unipi_name>(\w+))/(?P<unipi_dev>([a-zA-Z]+))\_(?P<unipi_circuit>[0-9_]+)/set$"
)


def _mqtt_client():
    """Singleton MQTT client"""
    global _MQTT_CLIENT
    if _MQTT_CLIENT is None:
        _MQTT_CLIENT = mqtt.Client()
    return _MQTT_CLIENT


def on_message(client, userdata, message):
    """Callback for MQTT events"""
    logger.debug(
        f"Incoming MQTT message for topic {message.topic} with payload {message.payload}"
    )
    match = MQTT_HASS_COMAND_TOPIC_REGEX.match(message.topic)
    if match is None:
        return

    # Update state topic
    topic = MQTT_HASS_STATE_TOPIC_FORMAT.format(**match.groupdict())
    _mqtt_client().publish(topic, message.payload)

    # Send to websocket
    value = 1 if message.payload == _settings().MQTT_PAYLOAD_ON else 0
    logger.info(
        "Push to output {dev}, {circuit}, {value}".format(
            dev=match.group("unipi_dev"),
            circuit=match.group("unipi_circuit"),
            value=value,
        )
    )
    asyncio.run(
        _ws_trigger(match.group("unipi_dev"), match.group("unipi_circuit"), value)
    )


def _subscribe_outputs_mqtt():
    """Subscribe all configuration for outputs to MQTT topics"""
    for config in _settings().CONFIGS:
        if config.unipi_dev in ("output"):
            _mqtt_client().subscribe(
                MQTT_HASS_COMMAND_TOPIC_FORMAT.format(
                    hass_type=config.hass_type,
                    unipi_name=_settings().UNIPI_NAME,
                    unipi_dev=config.unipi_dev,
                    unipi_circuit=config.unipi_circuit,
                )
            )


def _autodiscover_mqtt():
    """Push MQTT autodiscovery settings to MQTT broker"""
    for config in _settings().CONFIGS:
        _mqtt_client().publish(
            MQTT_HASS_CONFIG_TOPIC_FORMAT.format(
                hass_type=config.hass_type,
                unipi_name=_settings().UNIPI_NAME,
                unipi_dev=config.unipi_dev,
                unipi_circuit=config.unipi_circuit,
            ),
            payload=json.dumps(
                {
                    "name": config.hass_name,
                    "command_topic": MQTT_HASS_COMMAND_TOPIC_FORMAT.format(
                        hass_type=config.hass_type,
                        unipi_name=_settings().UNIPI_NAME,
                        unipi_dev=config.unipi_dev,
                        unipi_circuit=config.unipi_circuit,
                    ),
                    "state_topic": MQTT_HASS_STATE_TOPIC_FORMAT.format(
                        hass_type=config.hass_type,
                        unipi_name=_settings().UNIPI_NAME,
                        unipi_dev=config.unipi_dev,
                        unipi_circuit=config.unipi_circuit,
                    ),
                }
            ),
        )


def _parser():
    """Generate argument parser"""
    parser = argparse.ArgumentParser()
    parser.add_argument("evok_uri", help="unipi websocket URI")
    parser.add_argument("mqtt_host", help="MQTT broker host")
    parser.add_argument("unipi_name", help="Unique name for unipi device")
    parser.add_argument("--mqtt_port", type=int, default=1883)
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

    # Read configs
    logger.info("Read configs from file %s", args.config_file)
    configs = _read_config(args.config_file)

    # Set settings first time and don't change them anymore
    _set_settings(
        args.unipi_name,
        args.evok_uri,
        args.mqtt_host,
        args.mqtt_port,
        args.mqtt_payload_on,
        args.mqtt_payload_off,
        configs,
    )

    # MQTT initial setup
    logger.info("Connecting to MQTT broker %s", args.mqtt_host)
    _mqtt_client().connect(_settings().MQTT_HOST, _settings().MQTT_PORT)
    _subscribe_outputs_mqtt()
    _mqtt_client().on_message = on_message

    # Push autodiscovery
    logger.info("Pushing MQTT autodiscovery setup to HASS")
    _autodiscover_mqtt()

    # Loop
    logger.info("Starting websocket poll loop")
    _mqtt_client().loop_start()
    asyncio.get_event_loop().run_until_complete(_ws_loop())


if __name__ == "__main__":
    sys.exit(main())
