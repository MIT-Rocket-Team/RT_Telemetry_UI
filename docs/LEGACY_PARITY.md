# Legacy UI feature parity

The supported legacy wire commands, values and shortcuts remain implemented. Normal station profiles apply the current hardware roles: Launch has one telemetry board, exclusive uplink authority and a pending software-enable protocol; Away has downlink and a pointer. LIVE Launch rocket commands remain locked until that switch is defined. The former pages are distributed across the Launch Flight/Systems windows; Away telemetry is reduced, and Video is its own role. See [station modes](V0A_STATION_MODES.md) for placement and availability.

The previous `ground_station/UI.py`, `rocket.py`, and `pointer.py` are the behavioral reference. Existing source, images and planning documents are reference material; the current requested feature set takes precedence over historical controls. The original files are unchanged.

## Feature inventory

| Original function | Current location / behavior |
|---|---|
| Two serial selectors, Refresh, Connect, Disconnect | Role-specific connection cards; separate workers, 115200 8N1, original DTR/RTS defaults and read timeouts |
| Start/Stop Polling | Persistent header and original shortcut; connecting alone does not start polling; stopping polling leaves the port open and preserves applicable command gating |
| Start/Stop Logging | Persistent header and original shortcut; immediate start into a unique app-data session folder |
| Good and rejected telemetry CSVs | `telemetry.csv` and `telemetry_badpackets.csv`, all 43 original columns, alongside raw/SQLite/video recording |
| State, RSSIs, packet age, filtered/max altitude, roll, velocity, angle from vertical, temperature | Systems telemetry tables (Launch); main telemetry subset (Away); packet number additionally displayed |
| Rocket GPS fix, latitude/longitude, altitude/max altitude, horizontal/vertical precision, satellites | Systems telemetry tables (Launch); main telemetry subset (Away), all original precision retained |
| Four servo drives and degree conversions | Systems telemetry tables (Launch); main telemetry subset (Away); also available in GNC/actuators |
| Three cell voltages, thresholds/color cues | Systems telemetry tables (Launch); main telemetry subset (Away) |
| Six power rail requests/readbacks, total current, power temperature | Systems → power rails/BMS; 3.3 V request remains fixed on, Total remains read-only aggregate |
| BMS enabled/triggered status | Systems → power rails/BMS; all eight bits are visible |
| State advance, zero P/Y/Roll, altitude, velocity, servos, PD Activate | Systems → rocket commands |
| Roll servo angle entry/send | Systems → rocket commands; same float32 wire values |
| VTX power 1/3/5/8 W | Systems → rocket commands |
| Six pyro selections, continuity, arm/fire readbacks, resistance | Systems → recovery |
| ARM/FIRE selected pyros | Same channel mask and original confirmation/no-selection behavior |
| Piston, BP wells, tender descender, all recovery | Same four commands and individual confirmation dialogs |
| Manual antenna elevation/azimuth | Antenna pointer → Send; finite numerical entries sent directly, with no added calibration or envelope prerequisites |
| Pointer UP/DOWN/LEFT/RIGHT/ZERO | Original opcodes 1/2/3/4/5; enabled by the pointer connection independently of the ground board |
| Ground GPS display and freeze | Antenna pointer → Send to AntPtr / Unfreeze GPS |
| Live rocket tracking | Both boards plus polling, frozen ground coordinates, and valid rocket position; original 5 Hz coordinate calculation |
| Current local date/time and status | Persistent status bar, connection panel and Diagnostics |
| Every keyboard shortcut | Serial controls menu; preserved for the applicable station/role |

## Exact shortcuts

Qt uses the same `QKeySequence` strings as the original PyQt5 UI. On macOS these render and operate with Command for Ctrl and Option for Alt.

- `Ctrl+R`: refresh both port lists.
- `Ctrl+Alt+C`: toggle ground connection.
- `Shift+Ctrl+Alt+C`: toggle pointer connection.
- `Ctrl+Return`: toggle polling.
- `Ctrl+L`: toggle logging.
- `Ctrl+Up`, `Ctrl+Down`, `Ctrl+Right`, `Ctrl+Left`: native pointer direction commands.
- `Ctrl+0`: native pointer zero.

Actions are bound once, so repeated status refreshes cannot duplicate transmissions or connection toggles.

## Reference-based verification

`tests/fixtures/legacy_reference.json` retains outputs from the actual original class methods for supported commands: all 64 converter masks, each pyro channel and grouped masks, all VTX choices, state advances, the zero-roll alias and several signed roll-servo angles; eight pointer packet cases; three geographic tracking cases; every CSV column; and the ten shortcut strings read from the original UI. The fixture records SHA-256 hashes of all three reference files.

The fixture was captured from the original class methods with physical connection methods removed and in-memory byte streams substituted. The historical capture helper remains in the original repository; the retained fixture runs without that helper or the reference source folder.

Regression coverage checks the captured bytes and decoded values, actual Qt keyboard event dispatch across views, two OS pseudo-terminals, separate polling, independent board enablement, ground-GPS freeze/calculation, one-click logging, confirmation acceptance/rejection, disconnect/reconnect, CSV/raw recordings and rejected-packet isolation. All physical I/O in these tests terminates at virtual devices.

## Preserved conventions and bug fixes

- Tracking preserves the old frozen receiver latitude/longitude rounded to five decimals, pointer altitude fixed to zero, and rocket filtered barometric altitude. Mission trajectory datum settings do not change these legacy pointing commands.
- Receiver altitude display/CSV preserves the old signed-int32 read and division by 1000, despite the audited receiver firmware's unit discrepancy. Ground fix means trailer value 3.
- Good-packet CSV retains the original two unused zeros in armed/fired/resistance lists, and original servo degree rounding. Lists use ordinary Python numeric/boolean representations, readable without NumPy wrappers.
- Bad-packet CSV uses the same field interpretation as good packets. This fixes two old bad-packet-only inconsistencies: swapped maximum-altitude fields and a different ground-fix test. The `badpackets` column now counts rejected packets; the old counter remained at zero. Rejected packets remain isolated from live displays and replay samples.
- BMS status has eight columns rather than the old six-column allocation with eight labels. Cell voltages and pyro states retain distinct status colors.
- Logging no longer depends on launching from a particular working directory. The original schema is preserved inside the portable app's unique session directory.
- Old duplicate QAction signal connections are fixed; supported actions and key combinations are unchanged.

The full UI and wire behaviors are regression-tested. Physical board, camera and mount testing remains a separate hardware acceptance step. The new simulation/video/trajectory features remain additive. Native Mac/Windows packaging and packaged verification are described in [BUILD.md](BUILD.md).
