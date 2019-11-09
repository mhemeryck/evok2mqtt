#!/usr/bin/env python
import argparse
import asyncio
import json
import logging
import logging.config
import sys

import websockets

URI = "ws://tesla.lan/ws"

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


async def process(payload):
    obj = json.loads(payload)[0]
    logging.info(obj)


async def connect(uri):
    logging.info("Connecting to %s", uri)
    async with websockets.connect(uri) as websocket:
        while True:
            payload = await websocket.recv()
            await (process(payload))


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", help="unipi websocket URI", default=URI)
    return parser


def main():
    # Parse
    parser = _parser()
    args = parser.parse_args()

    # Loop
    asyncio.get_event_loop().run_until_complete(connect(args.uri))


if __name__ == "__main__":
    sys.exit(main())
