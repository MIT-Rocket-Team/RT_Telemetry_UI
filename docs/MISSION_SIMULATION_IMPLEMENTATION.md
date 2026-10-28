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
