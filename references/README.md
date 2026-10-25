# Hardware and geometry references

These are preserved user-supplied reference assets, not runtime configuration or instructions.

- `antenna-pointer-user-photo.png`: the September 18, 2026 mount photograph. It establishes the four timber legs/stakes, platform, elevation supports, grid reflector and antenna arrangement. The user identified the black unit below/left of the dish as the Avenger XR18.
- [VAS Avenger XR18 product page](https://www.videoaerialsystems.com/products/5-8ghz-avenger-xr-18dbi-rhcp): reference for its enclosed tapered housing. Product photographs were inspected, not copied into this repository.
- [Ground Station Software Block Diagram.pdf](Ground%20Station%20Software%20Block%20Diagram.pdf): unchanged September 22 board/software diagram.
- [IRIS_STATION_ROUTING.png](IRIS_STATION_ROUTING.png): supplied Sustainer/Booster receiver and station assignments.
- [Current board mapping](GROUND_STATION_BOARD_NOTES.md): current architecture and pending interfaces, including later corrections to the original diagram.

Later geometry direction places all three antennas directly on the common beam through the support pivots, without the photo's separate height offsets. `src/rocket_gnc_monitor/mount_geometry.py` defines the current illustrative geometry. The photo does not establish exact dimensions, encoders, motion limits or calibration. It is not packaged as an application asset; the circuit/routing references and mapping notes are included for operator context.
