# Ground-station hardware references

The [Ground Station Software Block Diagram.pdf](Ground%20Station%20Software%20Block%20Diagram.pdf) was supplied on September 22, 2026 and is preserved unchanged (SHA-256 `8685ef20c10e04ab3431a1737633fbc7a0a61eef363b93211ab654c8cf09f5aa`). It is historical design input, not an implemented interface contract. Later station-role decisions are reflected below and in the [station guide](../docs/V0A_STATION_MODES.md).

## Current physical layout

| Location | Connections represented by the current application |
| --- | --- |
| Launch Telemetry | One local telemetry PCB for downlink and future switch-enabled uplink. Ethernet/PoE to four LTU-XR paths; select an Away antenna route. No local pointer. |
| Away Telemetry | Downlink PCB and antenna-pointer PCB over separate USB serial links. Local WLAN via the Away network equipment. |
| Launch Video | One local Digital USB receiver; remote Analog/Digital station-source slots await their transfer protocol. |
| Away Video | Two USB receivers assigned by station/vehicle. Balius: Digital + Analog; Iris Away 1–3: Sustainer Digital + Sustainer Analog; Iris Away 4: Sustainer Digital + Booster Analog. |
| APRS | External radio/audio decoder (Dire Wolf) supplies KISS TCP to the monitor. |

The [Iris routing diagram](IRIS_STATION_ROUTING.png) establishes the Sustainer/Booster station assignments. Iris has three system video channels, while each Away station receives two. Launch displays all three and retains thumbnail slots for every valid Away source.

## Interfaces and limitations

- The audited Zephyrus serial downlink/pointer protocols are implemented. Launch software uplink enable and Iris target-selection switch commands are undefined and simulated only in DEMO. No guessed packets are sent.
- Wi-Fi selection records a desired SSID, displays the current OS connection separately and opens OS settings. It neither stores Wi-Fi credentials nor configures the LTU/AP hardware.
- The explicit one-peer LAN connection carries bounded, read-only telemetry/pointer/APRS snapshots. It carries no video, audio or commands, and remote values never enter the local hardware-control path.
- The Launch video wall provides source routing/promotion and stale/unavailable states. Remote video transfer and recording await their transport contract.
- LTU signal strength/management, analog VRX tuning and calls are not implemented. The diagrams supply no verified management/command interface for them.
- APRS accepts uncompressed AX.25 UI positions (`!`, `=`, `/`, `@`). Other encodings remain unsupported. APRS does not actuate the pointer; Dire Wolf is installed/configured separately.

Serial, local USB video, recorded data, manual/imported wind and bundled OpenRocket operate without an internet service. Online weather and initial build downloads need internet access. Physical receiver/board/radio/network acceptance remains separate from decoder, UI and package tests.
