# Recorded-flight regression fixtures

The test suite retains three receiver logs from the user-supplied directory
`/Users/mdn/Developer/ActiveControl_MIT_RktTeam/sim/FCsim/benchmarking/telem/`.
They are test data, not a selectable application mode or bundled production
recordings. Operators use LIVE telemetry or load a flight/session file in REPLAY.

`tests/fixtures/recorded_flight/` contains gzip-compressed copies with generic
`TEST_FLIGHT_GS1`, `TEST_FLIGHT_GS2` and `TEST_FLIGHT_GS3` names. Uncompressed CSV
content and SHA-256 hashes preserve the original observations; the source files
outside this repository are unchanged.

| Receiver | CSV rows | Receive duration |
|---|---:|---:|
| GS1 | 4,366 | 274.056 s |
| GS2 | 5,464 | 330.234 s |
| GS3 | 4,939 | 332.725 s |

These receivers observed the same launch. Their logs are not consecutive flight
segments and are not merged or deduplicated. Their host clocks differ by several
seconds. Original packet numbers, flight-computer milliseconds and receive UTC
remain available. The first FLIGHT packet is at device time 747.766 seconds in
all three logs; this state transition is not an independently measured liftoff.

All 43 original CSV fields are retained, including NumPy numeric/boolean
representations. `legacy_sample.py` decodes these literals without `eval`.
Typed values populate telemetry, servo, power/BMS and recovery fields without
inventing new GNC feedback. No raw radio frames accompany the CSVs, so packet
checksums cannot be verified again.

Known data limitations are retained for regression coverage:

- An approximately 8.4-second reception gap precedes the preflight transition.
- All logs contain periods without a 3D GPS fix. GS1/GS3 also contain GPS
  outliers, very large GPS altitudes and other corrupt readings. GS3 briefly
  reports PRE_FLIGHT during descent. These observations must not silently
  become valid positions or reconstructed flight states.
- GS3 has no valid ground-receiver GPS. GS1/GS2 retain receiver coordinates
  relevant to the original monitor's barometric-height pointing convention.
- Gyro saturation prevents reliable reconstruction of absolute orientation
  from the recorded cumulative gyro integrals.
- Zero battery-cell values remain unchanged. The recorded maximum barometric
  altitude is 5,549.227539 m. All three files end in MAIN descent; they contain
  no landing observation.
- No launch video or raw-radio capture was supplied with these CSV files.

The fixtures support decoder, CSV-import, timestamp, gap, provenance and replay
regression checks. They do not establish hardware acceptance, absolute attitude
accuracy, motor-burning status or confirmed parachute deployment.
