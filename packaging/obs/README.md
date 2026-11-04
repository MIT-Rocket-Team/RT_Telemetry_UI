# OBS presets

Generate local, credential-free profiles and scene collections with:

```bash
.venv/bin/python scripts/prepare_broadcast_obs.py --output build/obs-setup
```

The generator is the source of the templates; generated machine-specific file paths are not committed. It supplies:

- Rocket 1080p30: 1920×1080, H.264 CBR 14 Mb/s, 2 s keyframes.
- Rocket 720p30: 1280×720, H.264 CBR 5 Mb/s, 2 s keyframes.
- AAC stereo 128 kb/s, 48 kHz; MKV recording.
- Rocket Broadcast collection: independent image-based Emergency slate and a window-capture source awaiting local mapping. Initial scene is the slate.
- Optional local movie rehearsal collection (`--fixture /absolute/path/to/test.mkv`).

No profile contains service.json, account data, stream keys, passwords, microphone IDs or a preselected screen/window ID. The screen-capture source uses the OBS 33.0.1 macOS `screen_capture` Window mode. Windows/Linux need the platform's individual-window source instead.

The installer refuses to overwrite existing preset names. Preserve any customized presets before regenerating. See [the operator guide](../../docs/BROADCAST_OPERATOR_GUIDE.md) for exact window mapping, audio offsets, recording checks and shutdown order.

References: [OBS window source](https://obsproject.com/kb/macos-screen-capture-source), [recording guide](https://obsproject.com/kb/standard-recording-output-guide), [WebSocket protocol](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md).
