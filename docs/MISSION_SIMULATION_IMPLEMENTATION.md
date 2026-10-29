# Mission Simulation contract foundation (A1)

28 October 2026. This repository now includes `rocket_gnc_monitor.mission_contracts`, a standard-library-only contract layer shared with OpenRocket MIT. It provides validated mission drafts, exact URRG defaults, event-addressed deterministic sampling, explicit-height WGS84 geometry and bounded `rt-mission.v1` frame encoding/decoding. Importing it opens no devices, sockets, applications or Qt runtime. It does not change current LIVE/REPLAY controller behavior.

The authoritative JSON contracts live in OpenRocket's `core/src/main/resources/mission/v1/`. Identical reviewed copies are packaged under this module's `data/v1/`, with a SHA-256 inventory. Run:

```sh
.venv/bin/python scripts/sync_mission_contracts.py --check \
  ../ActiveControl_MIT_RktTeam/clone/openrocket/core/src/main/resources/mission/v1
.venv/bin/python -m pytest -q tests/test_mission_contracts.py tests/test_protocol.py tests/test_location.py
```

Omitting `--check` explicitly copies a verified source bundle. Both implementations test the same stored validation cases, wire bytes, random vectors and analytic geometry. Telemetry tests additionally compare URRG positions with the real MGRS decoder and pass legacy frames/commands through the existing protocol code.

Accepted drafts may still have no rocket and unresolved heights. Their unknown optional settings survive save/load. The current schemas validate the envelope and cross-references, not complete executable flight/codec/link settings. The bundled validator supports only the schema keywords used in these contracts and rejects unsupported keywords without resolving external references.

Simulation source/clock integration, a headless station worker, application launch/attachment, custom codec support, simulated video, result import and package/runtime qualification remain later increments. A1 does not make the UI a connected Mission Simulation participant and does not enable physical commands. The OpenRocket implementation ledger is `doc/improvements/18-mission-implementation-progress.md` in the sibling repository.


## Ordinary Base reception from OpenRocket (native Mission UI)

OpenRocket MIT now owns its configuration window and the simulated Away publishers.
No Telemetry UI application path or private attachment ticket is required. Open
Telemetry UI normally as Base, select the matching Balius/Iris vehicle, open Station
network and join a discovered **SIMULATION** station's mission. Joining a telemetry
publisher selects its receiver for read-only display. A video Base adopts the
advertised digital/analog stream registry and uses its existing leased SRT wall.
Leaving the simulation restores the ordinary video layout and local input controls.

`mission_network.py` is a Qt-free publisher bundled into OpenRocket alongside the
shared network runtime. Native OpenRocket controls use private process pipes. Public
services retain `_rt-gs._tcp.local.`, `/api/v1`, `rt-gs.v1`, source-mode labels, journal
barriers, boot identities and bounded queues. Stream sources contain the generated
slate pixels. Accepted samples retain original post-link bytes and mission time, while
network receipt ages use the host clock. No simulated sample enters local hardware
control. Commands remain in OpenRocket.

Only the selected ensemble member is shared. Pause preserves service and media
identities, continues heartbeats/leases and uses video carrier frames without creating
canonical samples or encoded recording frames. Optional bounded video recording is
shared by the desktop and headless publisher; OpenRocket adds completed segments to
its result archive. A station still supports two main and two preview media leases.

Station-slot conflicts are scoped by mission, source mode and vehicle. A simulated
GS1 can coexist with a live GS1 on the same network. Joining is explicit and simulation
acceptance is not saved across restarts. No remote pointer commands, private file
selection or executable-launch API is exposed. These are trusted-LAN services.

`bundle_mission_network.py` builds the deterministic headless source ZIP for OpenRocket.
`build_mission_srt.py` builds digest-verified SRT 1.5.4 on each qualification platform;
normal packaged applications include the resulting runtime. The existing attachment
workflow remains for compatibility tests, separate from this normal Base workflow.

Verification includes ordinary Base join controls, exact received bytes, read-only
display, custom streams through real leased SRT, pause/resume, encoded segments,
restoring the standard wall and scoped identity conflicts. Set
`RUN_MISSION_DISCOVERY=1` to additionally qualify multicast discovery on a local LAN
without manual endpoints. CI does not assume multicast routing between hosted jobs.
