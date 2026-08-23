"""A synthetic patient, for end-to-end runs against a live backend.

Joins the room the way `frontend/call` does — LiveKit data channel, one audio
track — plays a WAV, then listens. It exists because the properties worth
proving are all about the media path: that the assistant is already in the room
when the token arrives, that a red flag ends the call before any model runs, and
that what the patient hears is what the record says.

    uv run python tests/e2e/patient.py <roomName> [--say safety]
"""

import argparse
import array
import asyncio
import json
import sys
import wave
from pathlib import Path

from livekit import rtc

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.core.config import LIVEKIT_URL  # noqa: E402
from services.core.tokens import mint_token  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "speech-16k.wav"


async def run(room_name: str, listen_secs: float) -> dict:
    room = rtc.Room()
    messages: list[dict] = []
    audio = {"frames": 0, "ms": 0.0}

    @room.on("data_received")
    def _on_data(packet: rtc.DataPacket):
        try:
            messages.append(json.loads(packet.data.decode()))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    @room.on("track_subscribed")
    def _on_track(track, _pub, _participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        async def drain():
            async for event in rtc.AudioStream(track):
                audio["frames"] += 1
                audio["ms"] += (
                    event.frame.samples_per_channel / event.frame.sample_rate * 1000
                )

        asyncio.create_task(drain())

    await room.connect(
        LIVEKIT_URL, mint_token(room_name, "patient-pt_alice", can_publish=True)
    )
    source = rtc.AudioSource(16_000, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    print(f"[patient] joined {room_name}")

    # Let the assistant finish its opening before answering over the top of it.
    await asyncio.sleep(14)

    with wave.open(str(FIXTURE)) as w:
        pcm = array.array("h")
        pcm.frombytes(w.readframes(w.getnframes()))
        rate = w.getframerate()

    chunk = int(rate * 0.01)
    print(f"[patient] speaking {len(pcm) / rate:.2f}s")
    for i in range(0, len(pcm) - chunk, chunk):
        await source.capture_frame(
            rtc.AudioFrame(pcm[i : i + chunk].tobytes(), rate, 1, chunk)
        )
        await asyncio.sleep(0.01)

    # Silence, so the turn is endpointed rather than timing out.
    quiet = array.array("h", [0] * chunk)
    for _ in range(int(listen_secs * 100)):
        await source.capture_frame(rtc.AudioFrame(quiet.tobytes(), rate, 1, chunk))
        await asyncio.sleep(0.01)

    await room.disconnect()
    return {"messages": messages, "audio": audio}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("room")
    parser.add_argument("--listen", type=float, default=25.0)
    args = parser.parse_args()

    result = await run(args.room, args.listen)
    audio = result["audio"]
    print(f"\n[patient] assistant audio: {audio['ms'] / 1000:.1f}s")
    print(f"[patient] messages: {len(result['messages'])}")
    for m in result["messages"]:
        print("   ", json.dumps(m)[:120])


if __name__ == "__main__":
    asyncio.run(main())
