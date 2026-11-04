"""Credential-free OBS preset generation, shared by the app and setup CLI."""

import json
import shutil
import uuid
from pathlib import Path


def source(name, kind, settings):
    return {
        "name": name,
        "uuid": str(uuid.uuid4()),
        "id": kind,
        "versioned_id": kind,
        "settings": settings,
        "mixers": 1,
        "sync": 0,
        "flags": 0,
        "volume": 1.0,
        "balance": 0.5,
        "enabled": True,
        "muted": False,
        "monitoring_type": 0,
    }


def collection(name, inputs, scenes):
    sources = list(inputs)
    for title, items in scenes:
        entries = [
            {
                "name": item["name"],
                "source_uuid": item["uuid"],
                "id": i + 1,
                "visible": True,
                "locked": True,
                "pos": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rot": 0,
                "align": 5,
                "bounds_type": 2,
                "bounds": {"x": 1920, "y": 1080},
                "bounds_align": 0,
                "crop_left": 0,
                "crop_right": 0,
                "crop_top": 0,
                "crop_bottom": 0,
            }
            for i, item in enumerate(items)
        ]
        sources.append(
            source(title, "scene", {"id_counter": len(entries), "custom_size": False, "items": entries})
        )
    return {
        "name": name,
        "sources": sources,
        "groups": [],
        "scene_order": [{"name": s} for s, _ in scenes],
        "current_scene": scenes[0][0],
        "current_program_scene": scenes[0][0],
        "current_transition": "Cut",
        "transition_duration": 0,
        "transitions": [],
        "quick_transitions": [],
        "saved_projectors": [],
        "preview_locked": True,
        "version": 2,
    }


def profile(name, width, height, bitrate, recordings):
    return f"""[General]
Name={name}
[Output]
Mode=Advanced
FilenameFormatting=Rocket-%CCYY-%MM-%DD_%hh-%mm-%ss
Reconnect=true
RetryDelay=2
MaxRetries=25
[AdvOut]
Encoder=obs_x264
ApplyServiceSettings=true
TrackIndex=1
RecType=Standard
RecEncoder=none
RecFilePath={recordings}
RecFormat2=mkv
RecTracks=1
Track1Bitrate=128
[Video]
BaseCX=1920
BaseCY=1080
OutputCX={width}
OutputCY={height}
FPSType=0
FPSCommon=30
ScaleType=bicubic
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial
[Audio]
SampleRate=48000
ChannelSetup=Stereo
"""


def prepare(destination, assets, recordings, fixture=None):
    destination, assets, recordings = map(Path, (destination, assets, recordings))
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Choose an empty output folder; existing OBS files are not overwritten")
    if not (assets / "standby.png").is_file():
        raise ValueError("The bundled emergency slate is missing")
    destination.mkdir(parents=True, exist_ok=True)
    artwork = destination / "assets"
    artwork.mkdir()
    shutil.copy2(assets / "standby.png", artwork / "standby.png")
    (destination / "README.txt").write_text(
        "Import a profiles/ folder and scenes/Rocket_Broadcast.json in OBS.\n"
        "Keep this export folder in place: the independent emergency slate uses assets/standby.png.\n"
        "If moving to another computer, remap that image and the recording folder in OBS.\n"
        "Select the individual Rocket Broadcast — Program window and local audio inputs.\n"
        "No account, stream key, password or device selection is included.\n"
    )
    recordings.mkdir(parents=True, exist_ok=True)
    for name, width, height, bitrate in [
        ("Rocket 1080p30", 1920, 1080, 14000),
        ("Rocket 720p30", 1280, 720, 5000),
    ]:
        directory = destination / "profiles" / name.replace(" ", "_")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "basic.ini").write_text(profile(name, width, height, bitrate, recordings))
        (directory / "streamEncoder.json").write_text(
            json.dumps(
                {
                    "rate_control": "CBR",
                    "bitrate": bitrate,
                    "keyint_sec": 2,
                    "preset": "veryfast",
                    "profile": "high",
                },
                indent=2,
            )
        )
        # No service.json: OBS is intentionally left without an account or key.
    scene_dir = destination / "scenes"
    scene_dir.mkdir(exist_ok=True)
    slate = source(
        "Rocket emergency slate", "image_source", {"file": str((artwork / "standby.png").resolve())}
    )
    capture = source(
        "Map Rocket Broadcast — Program window",
        "screen_capture",
        {"type": 1, "window": 0, "show_cursor": False, "show_hidden_windows": False},
    )
    production = collection(
        "Rocket Broadcast", [slate, capture], [("Emergency slate", [slate]), ("Rocket Program", [capture])]
    )
    (scene_dir / "Rocket_Broadcast.json").write_text(json.dumps(production, indent=2))
    if fixture:
        movie = source(
            "LOCAL TEST MOVIE — NOT LIVE",
            "ffmpeg_source",
            {
                "is_local_file": True,
                "local_file": str(fixture.resolve()),
                "looping": True,
                "restart_on_activate": True,
                "clear_on_media_end": False,
                "close_when_inactive": False,
                "hw_decode": True,
            },
        )
        rehearsal = collection(
            "Rocket Local Rehearsal",
            [movie, slate],
            [("LOCAL FIXTURE — NOT LIVE", [movie]), ("Emergency slate", [slate])],
        )
        (scene_dir / "Rocket_Local_Rehearsal.json").write_text(json.dumps(rehearsal, indent=2))
