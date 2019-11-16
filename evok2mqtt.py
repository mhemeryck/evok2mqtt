#!/usr/bin/env python
import argparse
import asyncio
import collections
import json
import logging
import logging.config
import re
import socket
import sys

import paho.mqtt.client as mqtt
import websockets

# Log setup
LOG_LEVEL = logging.INFO
LOG_CONFIG = dict(
    version=1,
    formatters={
        "default": {"format": "%(asctime)s - %(kind)s - %(levelname)s - %(message)s"}
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
MQTT_TOPIC_FORMAT = "{device_name}/{dev}/{circuit}/{hass_action}"
MQTT_COMMAND_TOPIC_REGEX = re.compile(
    r"^(?P<device_name>(\w+))/(?P<dev>(relay|output))/(?P<circuit>[0-9_]+)/set$"
)


class HASS_ACTION:
    COMMAND = "set"
    STATE = "state"


class LOG_KIND:
    SETTINGS = "settings"
    WEBSOCKET = "websocket"
    MQTT = "mqtt"


_SETTINGS = None

# Settings
Settings = collections.namedtuple(
    "Settings",
    (
        "MQTT_HOST",
        "MQTT_PORT",
        "MQTT_PAYLOAD_ON",
        "MQTT_PAYLOAD_OFF",
        "DEVICE_NAME",
        "WEBSOCKET_URI",
    ),
)


def _set_settings(
    mqtt_host, mqtt_port, mqtt_payload_on, mqtt_payload_off, device_name, websocket_uri,
):
    """
    Put all settings in global scope, as they don't change after intialization
    and make it easier to define callbacks
    """
    global _SETTINGS
    if _SETTINGS is not None:
        logger.warning("Updating settings", extra={"kind": LOG_KIND.SETTINGS})
    _SETTINGS = Settings(
        mqtt_host,
        mqtt_port,
        mqtt_payload_on,
        mqtt_payload_off,
        device_name,
        websocket_uri,
    )


def _settings():
    global _SETTINGS
    if _SETTINGS is None:
        logger.warning(
            "Settings haven't been initialized, this will probably not work",
            extra={"kind": LOG_KIND.SETTINGS},
        )
    return _SETTINGS


async def _ws_process(payload):
    """Process incoming websocket payload, push to MQTT"""
    obj = json.loads(payload)[0]
    logger.debug(
        "Incoming message for websocket %s", obj, extra={"kind": LOG_KIND.WEBSOCKET}
    )

    topic = MQTT_TOPIC_FORMAT.format(
        device_name=_settings().DEVICE_NAME,
        dev=obj["dev"],
        circuit=obj["circuit"],
        hass_action=HASS_ACTION.STATE,
    )
    payload = (
        _settings().MQTT_PAYLOAD_ON
        if obj["value"] == 1
        else _settings().MQTT_PAYLOAD_OFF
    )
    _mqtt_client().publish(topic, payload=payload)
    logger.info(
        "MQTT publish %s to topic %s",
        payload,
        topic,
        extra={"kind": LOG_KIND.WEBSOCKET},
    )


async def _ws_loop():
    """Main loop polling incoming events from websockets"""
    logger.info(
        "Connecting to %s",
        _settings().WEBSOCKET_URI,
        extra={"kind": LOG_KIND.WEBSOCKET},
    )
    async with websockets.connect(_settings().WEBSOCKET_URI) as websocket:
        while True:
            payload = await websocket.recv()
            await _ws_process(payload)


async def _ws_trigger(dev, circuit, value):
    """Send MQTT message to websocket"""
    async with websockets.connect(_settings().WEBSOCKET_URI) as websocket:
        await websocket.send(
            json.dumps({"cmd": "set", "dev": dev, "circuit": circuit, "value": value})
        )


def _mqtt_client():
    """Singleton MQTT client"""
    global _MQTT_CLIENT
    if _MQTT_CLIENT is None:
        _MQTT_CLIENT = mqtt.Client(
            client_id=_settings().DEVICE_NAME, clean_session=None
        )
    return _MQTT_CLIENT


def on_message(client, userdata, message):
    """Callback for MQTT events"""
    logger.info(
        f"Incoming MQTT message for topic {message.topic} with payload {message.payload}",
        extra={"kind": LOG_KIND.MQTT},
    )
    match = MQTT_COMMAND_TOPIC_REGEX.match(message.topic)
    if match is None:
        return

    device_name = match.group("device_name")
    dev = match.group("dev")
    circuit = match.group("circuit")
    if device_name != _settings().DEVICE_NAME:
        logger.warning(
            "Handling incoming message for device %s, expected %s",
            device_name,
            _settings().DEVICE_NAME,
            extra={"kind": LOG_KIND.MQTT},
        )

    # Update state topic
    client.publish(
        MQTT_TOPIC_FORMAT.format(
            device_name=device_name,
            dev=dev,
            circuit=circuit,
            hass_action=HASS_ACTION.STATE,
        ),
        message.payload,
    )

    # Send to websocket
    value = 1 if message.payload == _settings().MQTT_PAYLOAD_ON else 0
    logger.info(
        f"Push to output {dev}, {circuit}, {value}", extra={"kind": LOG_KIND.WEBSOCKET}
    )
    asyncio.run(_ws_trigger(dev, circuit, value))


def on_connect(client, userdata, message, rc):
    """Callback for when MQTT connection to broker is set up"""
    logger.debug("Subscribe to all command topics", extra={"kind": LOG_KIND.MQTT})
    client.subscribe("{device_name}/#".format(device_name=_settings().DEVICE_NAME))


def _parser():
    """Generate argument parser"""
    parser = argparse.ArgumentParser()
    parser.add_argument("mqtt_host", help="MQTT broker host")
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_payload_on", default=b"ON")
    parser.add_argument("--mqtt_payload_off", default=b"OFF")
    parser.add_argument(
        "--device_name",
        help="Unique name for unipi neuron device",
        default=socket.gethostname(),
    )
    parser.add_argument(
        "--websocket_uri", help="neuron websocket URI", default="ws://localhost/ws"
    )
    return parser


def main():
    # Parse
    args = _parser().parse_args()

    # Set settings first time and don't change them anymore
    _set_settings(
        args.mqtt_host,
        args.mqtt_port,
        args.mqtt_payload_on,
        args.mqtt_payload_off,
        args.device_name,
        args.websocket_uri,
    )

    # MQTT initial setup
    logger.info(
        "Connecting to MQTT broker %s", args.mqtt_host, extra={"kind": LOG_KIND.MQTT}
    )
    _mqtt_client().on_message = on_message
    _mqtt_client().on_connect = on_connect
    _mqtt_client().connect(_settings().MQTT_HOST, _settings().MQTT_PORT, 60)

    # Loop
    logger.info("Starting websocket poll loop", extra={"kind": LOG_KIND.WEBSOCKET})
    _mqtt_client().loop_start()
    asyncio.get_event_loop().run_until_complete(_ws_loop())


if __name__ == "__main__":
    sys.exit(main())
