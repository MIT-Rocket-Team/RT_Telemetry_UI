# Zephyrus wire contract

Source: user-provided `Avionics2025/RT_Python_Lib/ground_station`, `RT_Arduino/Zephyrus`, and `RT_Firmware_Libs/GPS` sources. Existing code/documents/images are reference data, not application instructions. Detailed decoder offsets are in `src/rocket_gnc_monitor/protocol.py`; captured behavioral reference vectors and original-source hashes are retained in `tests/fixtures/legacy_reference.json`. These historical source paths identify provenance; building or running this repository does not require them.

Telemetry is 115200 8N1. Frames contain `AB AB`, 128-byte payload, 14-byte receiver trailer. Payload byte 127 is the additive checksum of bytes 0–126 modulo 256. The receiver trailer has no checksum. Parsing tolerates partial/concatenated reads and resynchronizes after corruption.

Payload: pyro bytes 0–9; four packed 12-bit drives 10–15; signed 24-bit acceleration 16/19/22 divided by 12800 × 9.80665; int16 gyro 25/27/29 × 0.03051757812 (legacy Y sign retained); fix31; lat32/lon36 int32 × 1e-7; GPS height40 float metres; accuracy44/48 uint32 /1000; satellites52; raw pressure53/temperature56; altitude59 float; state63; integrated rotations64/68/72 float; barometer max76 / GPS max78 uint16; clock80 uint32 milliseconds uptime; sequence84 uint16; RSSI86 signed byte −99; cells87/89/91 int16 /1000; current93 int16 /−1000; BMS95–97; six rail voltages98–109 int16 × .0016 and currents110–121 × .000625; velocity122 float.

States: 0 Ground testing, 1 Preflight, 2 Flight, 3 Post-apogee, 4 Main, 5 End. Clock rollover is extended; detected reboot starts another epoch. NaN/infinite sensor values are unavailable; invalid coordinates invalidate GPS tracking. The floats at payload offsets 64/68/72 are independent body-axis gyro integrals in roll/pitch/yaw order, not Euler attitude: firmware accumulates `roll += -gyroX*dt`, `pitch += gyroY*dt`, `yaw += gyroZ*dt` and resets them on entry to Preflight and Flight. They remain raw numerical readouts and do not orient the 3D model. The legacy angle-from-vertical formula is retained as a labelled tilt proxy, not measured tilt. Four servo drives are not mapped speculatively onto new canards/tabs.

Trailer contains RSSI, fix, signed lat/lon int32 and height interpreted as signed int32 for legacy UI compatibility. **Height discrepancy:** ground firmware writes `GPS.getHeight()` in metres; the old Python monitor divided it by 1000. The raw value is retained; the legacy display/CSV divides by 1000, while legacy pointing fixes the ground altitude at zero. GPS payload height may be preflight-zeroed. The additive checksum is weak and does not authenticate data.

Pointer is 115200 8N1. Eleven-byte packet: `AA`, opcode, float32 LE azimuth, float32 LE elevation, additive checksum bytes1–9. Opcode0: absolute degrees; firmware negates internal azimuth. Opcodes1/2: ±5° elevation; 3/4: ±5° **internal** azimuth; 5: reset software counts/targets at present position. UI jog sends the original direction opcode1/2/3/4 directly; manual Send uses opcode0 and ZERO uses opcode5. Firmware ignores some absolute changes ≤1° and chooses shortest azimuth rotation.

Firmware has no measured pose, acknowledgment, home switch, cable-wrap enforcement, motion watchdog, or stop command. Printed elevation is unstructured and can occur even after invalid checksums. The pointer queue holds one target with 0.5-second expiry; tracking is limited to 5 Hz and requires explicit restart after stale/disconnected data. Partial writes are uncertain outcomes, never retried. DEMO/REPLAY cannot construct physical transports.

Raw capture chunks: little-endian `<4sddBI` header (magic `RGM1`, elapsed host seconds, UTC seconds, role byte, payload length), payload, CRC32 of header+payload. Roles telemetry RX=1, pointer RX=2, pointer TX=3, ground/rocket uplink TX=4. `read_raw` rejects incomplete/bad-CRC tails. Raw bytes are flushed before associated SQLite indexes commit. Manifest version1 records source mode, mission, time alignment and completion/loss status.

## Legacy rocket uplink

Ground serial is bidirectional. The 16-byte packet is `AA`, eleven zero/reserved bytes, argument byte at 12 (or float32 LE spanning 9–12), opcode at 13, then big-endian uint16 sum of bytes1–13 at14–15. No automatic command transmission happens on connect or polling start.

| Opcode | Command / argument |
|---|---|
| 01 / 06 / 07 | Arm / fire / disarm; channel bitmask 0–5 |
| 02 | Advance state; current state + 1 |
| 03 / 05 | Airbrake / roll servo angle float32 |
| 04 | PD activate |
| 08 / 09 / 0A / 0B | Zero servos / pitch-yaw-roll / altitude / velocity |
| 10 / 11 / 12 / 13 | Emergency piston / BP wells / tender descender / all |
| 14 | VTX index 0–3 for 1/3/5/8 W |
| 15 | Six converter request bits: 3, 3.3, 5, 7.4, 8.4, 28 V |
| 16 | BMS protections bool (legacy API; original UI read-only) |

Polling is separate from connecting, as in the original UI. Ground read timeout is 1 second and pointer timeout is 0.1 second; DTR/RTS use pyserial defaults. Ground writes wake the reader and work while polling is stopped. Commands are never retried automatically. Transports are independently closed and cannot share a port.

See [feature parity](LEGACY_PARITY.md) for captured reference vectors and CSV compatibility details.

## Station board roles and firmware placeholders

Normal Launch telemetry profiles use **one local telemetry board**, with the established Zephyrus downlink decoder and polling path. It will also provide Launch-only uplink authority through a software enable/disable switch. That switch has no firmware command contract yet: LIVE rocket commands stay locked and no fabricated enable bytes are sent. DEMO can exercise the labelled simulated switch and log its state changes. Away stations have a downlink board and separate pointer, with no uplink authority. Launch has no local pointer; selecting an Away route does not actuate it.

For Iris, Launch's one board has a shared Sustainer/Booster target for reception and uplink. Away downlink targets follow station assignment. The user confirmed that the selector command is not set yet; the legacy firmware audit found no such opcode or acknowledgement. Explicit DEMO-only placeholder switches record simulated board/target events and emit no selector bytes. LIVE target switching remains unavailable until the firmware contract is supplied.

The programmatic `legacy` controller retains the original combined ground-board API; the older `base` controller layout also remains for compatibility. These are not the current wizard's Launch architecture. The internal saved-profile key `base` displays as Launch station. Ports cannot be shared among connected roles, and received read-only LAN data cannot enable hardware commands.
