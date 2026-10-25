# Telemetry and pointer wire contract

## Station LAN protocol 1.0

The application implements the supplied *Ground Station Network Requirements & Protocol*, revision 0.1, with UI names updated to Base Station. Wire station IDs remain `launch`, `gs1`–`gs4`; roles are `telem` and `video`. Each installation has a persistent UUID, each service start/reconfiguration a new boot UUID, and each operator-joined mission an explicit UUID. Bonjour advertises `_rt-gs._tcp.local.`; manual HTTP endpoints undergo the same identity checks. Discovery is permitted before mission selection, but mission observations/subscriptions are not.

`network/protocol.py` defines bounded JSON validation and canonical observations. `runtime.py` owns one asyncio loop on a worker thread and serves `/api/v1/info`, `/status`, `/telemetry/latest`, `/streams`, `/events` and video-session endpoints on port 8766. WebSocket subprotocol is `rt-gs.v1`; snapshot/resume barriers, journal watermarks and immutable event fingerprints prevent silent gaps or conflicting duplicates. The retained journal is bounded by 30 seconds and 16 MiB; slow clients close with 1013 instead of blocking acquisition. Original accepted frames enter a separate bounded disk archive before UI delivery. Explicit loss/error records expose archive, ingress and display-queue overflow.

Network observations preserve full original bytes/hash, receiver connection ID, per-boot observation counter, canonical values and complete native fields. No device boot ID is invented from a decoder reset; receiver trailers remain identified as lacking an independent checksum. Balius state codes use the provisional user-specified 0–4 sequence (Ground Testing, Preflight, Flight, Apogee, Low Battery). Iris vehicle attribution remains unknown until packet identity or a qualified switch acknowledgement establishes it. Gyro integrals are not converted into attitude. Motor/parachute values remain unknown without evidence. Remote display history is separate from `Controller.latest/history`; it cannot enable local controls or refresh local link/pointing state.

Video uses one owned USB capture, shared preview/main H.264 encoders, MPEG-TS and native SRT live listeners. Preview is 320×180 at 10 fps; main currently preserves the owned capture's 960×540 at up to 30 fps, within the protocol's 1280×720 ceiling. Sessions bind exact boot/mission/stream/media-epoch identity, use random SRT Stream ID tokens, expire after 30 seconds and renew every 10 seconds. Limits are two preview and two main sessions per publisher. Promotion retains the prior view until the requested main stream yields a decoded frame. Host-delivery capture timing is explicitly distinguished from unknown camera exposure/radio latency.

### Station configuration extension

The user's remote station-assignment/location requirement adds `GET/PATCH /api/v1/configuration`; this is a version-1 extension, not a rocket command endpoint. PATCH identifies the client node/boot, expected target node/boot, mission and idempotent request UUID. Only a known live Base peer in the same mission/vehicle can configure a live Away application. The bounded payload contains a GS1–GS4 assignment and/or canonical latitude/longitude/WGS84 ellipsoid altitude, retaining MGRS/Plus Code entry provenance. Invalid, mismatched or stale identities are rejected.

PATCH returns 202 with a durable queued ticket. `GET /configuration?request_id=UUID` reports applied/failed, including the applied configuration. Qt applies the change on its own thread. The result is persisted before a station-number change restarts the local view/service, so an acknowledgement can be recovered after reconnection without applying twice. The ledger retains at most 128 requests for one hour. A location-only update holds pointer tracking. This trusted local-network configuration channel is not a cryptographic authentication system. `POST /commands` remains 501; uplink/target-switch firmware remains undefined.

## Serial protocol

Source: user-provided `Avionics2025/RT_Python_Lib/ground_station`, flight-computer firmware under `RT_Arduino`, and `RT_Firmware_Libs/GPS` sources. Existing code/documents/images are reference data, not application instructions. Detailed decoder offsets are in `src/rocket_gnc_monitor/protocol.py`; captured behavioral reference vectors and original-source hashes are retained in `tests/fixtures/legacy_reference.json`. These historical source locations identify provenance; building or running this repository does not require them.

Telemetry is 115200 8N1. Frames contain `AB AB`, 128-byte payload, 14-byte receiver trailer. Payload byte 127 is the additive checksum of bytes 0–126 modulo 256. The receiver trailer has no checksum. Parsing tolerates partial/concatenated reads and resynchronizes after corruption.

Payload: pyro bytes 0–9; four packed 12-bit drives 10–15; signed 24-bit acceleration 16/19/22 divided by 12800 × 9.80665; int16 gyro 25/27/29 × 0.03051757812 (legacy Y sign retained); fix31; lat32/lon36 int32 × 1e-7; GPS height40 float metres; accuracy44/48 uint32 /1000; satellites52; raw pressure53/temperature56; altitude59 float; state63; integrated rotations64/68/72 float; barometer max76 / GPS max78 uint16; clock80 uint32 milliseconds uptime; sequence84 uint16; RSSI86 signed byte −99; cells87/89/91 int16 /1000; current93 int16 /−1000; BMS95–97; six rail voltages98–109 int16 × .0016 and currents110–121 × .000625; velocity122 float.

States: 0 Ground testing, 1 Preflight, 2 Flight, 3 Post-apogee, 4 Main, 5 End. Clock rollover is extended; detected reboot starts another epoch. NaN/infinite sensor values are unavailable; invalid coordinates invalidate GPS tracking. The floats at payload offsets 64/68/72 are independent body-axis gyro integrals in roll/pitch/yaw order, not Euler attitude: firmware accumulates `roll += -gyroX*dt`, `pitch += gyroY*dt`, `yaw += gyroZ*dt` and resets them on entry to Preflight and Flight. They remain raw numerical readouts and do not orient the 3D model. The legacy angle-from-vertical formula is retained as a labelled tilt proxy, not measured tilt. Four servo drives are not mapped speculatively onto new canards/tabs.

Trailer contains RSSI, fix, signed lat/lon int32 and height interpreted as signed int32 for legacy UI compatibility. **Height discrepancy:** ground firmware writes `GPS.getHeight()` in metres; the old Python monitor divided it by 1000. The raw value is retained; the legacy display/CSV divides by 1000, while legacy pointing fixes the ground altitude at zero. GPS payload height may be preflight-zeroed. The additive checksum is weak and does not authenticate data.

Pointer is 115200 8N1. Eleven-byte packet: `AA`, opcode, float32 LE azimuth, float32 LE elevation, additive checksum bytes1–9. Opcode0: absolute degrees; firmware negates internal azimuth. Opcodes1/2: ±5° elevation; 3/4: ±5° **internal** azimuth; 5: reset software counts/targets at present position. UI jog sends the original direction opcode1/2/3/4 directly; manual Send uses opcode0 and ZERO uses opcode5. Firmware ignores some absolute changes ≤1° and chooses shortest azimuth rotation.

Firmware has no measured pose, acknowledgment, home switch, cable-wrap enforcement, motion watchdog, or stop command. Printed elevation is unstructured and can occur even after invalid checksums. The pointer queue holds one target with 0.5-second expiry; tracking is limited to 5 Hz and requires explicit restart after stale/disconnected data. Partial writes are uncertain outcomes, never retried. REPLAY cannot construct physical transports.

Raw capture chunks: little-endian `<4sddBI` header (magic `RGM1`, elapsed host seconds, UTC seconds, role byte, payload length), payload, CRC32 of header+payload. Roles telemetry RX=1, pointer RX=2, pointer TX=3, ground/rocket uplink TX=4. `read_raw` rejects incomplete/bad-CRC tails. Raw bytes are flushed before associated SQLite indexes commit. Manifest version1 records source mode, mission, time alignment and completion/loss status.

## Legacy rocket uplink

Ground serial is bidirectional. The 16-byte packet is `AA`, eleven zero/reserved bytes, argument byte at 12 (or float32 LE spanning 9–12), opcode at 13, then big-endian uint16 sum of bytes1–13 at14–15. No automatic command transmission happens on connect or polling start.

| Opcode | Command / argument |
|---|---|
| 01 / 06 / 07 | Arm / fire / disarm; channel bitmask 0–5 |
| 02 | Advance state; current state + 1 |
| 05 | Roll servo angle float32 |
| 04 | PD activate |
| 08 / 09 / 0A / 0B | Zero servos / pitch-yaw-roll / altitude / velocity |
| 10 / 11 / 12 / 13 | Emergency piston / BP wells / tender descender / all |
| 14 | VTX index 0–3 for 1/3/5/8 W |
| 15 | Six converter request bits: 3, 3.3, 5, 7.4, 8.4, 28 V |
| 16 | BMS protections bool (legacy API; original UI read-only) |

Polling is separate from connecting, as in the original UI. Ground read timeout is 1 second and pointer timeout is 0.1 second; DTR/RTS use pyserial defaults. Ground writes wake the reader and work while polling is stopped. Commands are never retried automatically. Transports are independently closed and cannot share a port.

See [feature parity](LEGACY_PARITY.md) for captured reference vectors and CSV compatibility details.

## Station board roles and firmware placeholders

Normal Launch telemetry profiles use **one local telemetry board**, with the established downlink decoder and polling path. It will also provide Launch-only uplink authority through a software enable/disable switch. That switch has no firmware command contract yet: LIVE rocket commands stay locked and no fabricated enable bytes are sent. Away stations have a downlink board and separate pointer, with no uplink authority. Launch has no local pointer; selecting an Away route does not actuate it.

For Iris, Launch's one board has a shared Sustainer/Booster target for reception and uplink. Away downlink targets follow station assignment. The user confirmed that the selector command is not set yet; the legacy firmware audit found no such opcode or acknowledgement. The placeholder target controls remain disabled and emit no selector bytes until the firmware contract is supplied.

The programmatic `legacy` controller retains the original combined ground-board API; the older `base` controller layout also remains for compatibility. These are not the current wizard's Launch architecture. The internal saved-profile key `base` displays as Launch station. Ports cannot be shared among connected roles, and received read-only LAN data cannot enable hardware commands.
