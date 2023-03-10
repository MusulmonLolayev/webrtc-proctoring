import asyncio
from utils.logger import LogConfig
from logging.config import dictConfig
import logging
import os
import uuid
from time import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from proctoring.simple_facerec import SimpleFacerec

from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole, MediaRelay

ROOT = os.path.dirname(__file__)

pcs = set()
relay = MediaRelay()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

dictConfig(LogConfig().dict())
logger = logging.getLogger("proctoring.log")


class VideoTransformTrack(MediaStreamTrack):
    """
    A video stream track that transforms frames from an another track.
    """

    kind = "video"

    def __init__(self, track, user_id):
        super().__init__()  # don't forget this!
        self.track = track
        self.user_id = user_id
        self.sfr = SimpleFacerec()
        self.sfr.load_encoding_images("images/")

    async def recv(self):
        start = time()
        frame = await self.track.recv()
        end = time()
        print("Frame recv: ", end - start)
        logger.info(f"Frame recv: {end - start}")
        start = time()
        img = frame.to_ndarray(format="rgb24")
        end = time()
        print("Time for rgb24: ", end - start)
        logger.info(f"Time for rgb24:  {end - start}")
        # Detect faces
        # start = time()
        # face_location, face_names = self.sfr.detect_known_faces(img)
        # end = time()
        # print("Time for rec: ", end - start)
        # logger.info(f"Time for rec: {end - start}")
        # for face_loc, name in zip(face_location, face_names):
        #     y1, x1, y2, x2 = face_loc[0], face_loc[1], face_loc[2], face_loc[3]
        #     print(name, self.user_id)
        #     logger.info(f"{name} -- {self.user_id}")

        #     if name.lower() == self.user_id.lower():
        #         text = "Tasdiqlandi"
        #         print("True")
        #     else:
        #         text = "Tasdiqlanmadi! Qayta urinib ko'ring"
        #         print("False")

        return frame

@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", context={'request': request})

@app.post('/offer')
async def offer(request: Request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pc_id = "PeerConnection(%s)" % uuid.uuid4()
    pcs.add(pc)

    def log_info(msg, *args):
        logger.info(pc_id + " " + msg, *args)

    log_info("Created for %s", request.client)
        
    recorder = MediaBlackhole()

    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            if isinstance(message, str) and message.startswith("ping"):
                channel.send("pong" + message[4:])

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        log_info("Connection state is %s", pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    @pc.on("track")
    def on_track(track):
        log_info("Track %s received", track.kind)

        if track.kind == "audio":
            # pc.addTrack(player.audio)
            recorder.addTrack(track)
        elif track.kind == "video":
            pc.addTrack(
                VideoTransformTrack(
                    relay.subscribe(track),
                    user_id=params["user_id"]
                )
            )
            # if args.record_to:
            #     recorder.addTrack(relay.subscribe(track))

        @track.on("ended")
        async def on_ended():
            log_info("Track %s ended", track.kind)
            await recorder.stop()

    # handle offer
    await pc.setRemoteDescription(offer)
    await recorder.start()

    # send answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

@app.on_event('shutdown')
async def on_shutdown():
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()