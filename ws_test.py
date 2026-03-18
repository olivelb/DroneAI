import asyncio
import websockets

async def test_ws():
    async with websockets.connect("ws://127.0.0.1:30080/ws/status") as ws:
        print("Connected!")
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            print(f"Received immediately: {msg}")
        except asyncio.TimeoutError:
            print("No messages received.")

asyncio.run(test_ws())
