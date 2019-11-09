#!/usr/bin/env python
import asyncio
import json

import websockets

URI = "ws://tesla.lan/ws"


async def process(payload):
    obj = json.loads(payload)[0]
    print(obj)


async def main():
    async with websockets.connect(URI) as websocket:
        while True:
            payload = await websocket.recv()
            await (process(payload))


asyncio.get_event_loop().run_until_complete(main())
