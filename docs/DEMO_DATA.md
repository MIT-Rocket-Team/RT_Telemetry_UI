# Zephyrus recorded test-flight demo

User-supplied source directory:
`/Users/mdn/Developer/ActiveControl_MIT_RktTeam/sim/FCsim/benchmarking/telem/`

The original files are unchanged. `resources/demo/` contains gzip-compressed,
byte-identical copies and a manifest with their uncompressed SHA-256 hashes.
They are bundled into both native builds; no source-directory access or network
is needed on the production machine.

| Recording | CSV rows | Receive duration | Default |
|---|---:|---:|---|
| ZEPH_TEST_FLIGHT_GS1.csv | 4,366 | 274.056 s | |
| ZEPH_TEST_FLIGHT_GS2.csv | 5,464 | 330.234 s | Yes |
| ZEPH_TEST_FLIGHT_GS3.csv | 4,939 | 332.725 s | |

These are three receivers observing the same launch, not consecutive flight
segments. They are never merged or deduplicated. Their host clocks differ by
several seconds. Each log plays according to its own receive timestamp deltas;
original packet numbers, flight-computer milliseconds and receive UTC are retained.
The first FLIGHT packet is at device time 747.766 seconds for all three logs.
Flight-time display is aligned to this recorded state transition, not an independently
measured liftoff. The default cue is five receive-time seconds before that transition.

GS2 contains the most samples and avoids the large GPS corruptions present in
GS1/GS3. No raw radio frames accompany these CSVs, so checksums cannot be verified
again. All 43 original CSV fields are retained, including NumPy numeric/boolean
representations, in each sample's `legacy_csv` detail. Typed values populate the
original telemetry, servo, power/BMS and recovery panels. No new GNC feedback is
invented. CSV import also uses this complete field mapping.

Quality and presentation:

- The logs contain an approximately 8.4-second reception gap before the preflight
  transition. Playback retains gaps and never fabricates intermediate packets.
- All contain periods without a 3D GPS fix. The local track excludes those points.
- GS1/GS3 include GPS outliers, huge GPS-altitude values and other corrupted readings;
  GS3 briefly reports PRE_FLIGHT during descent. Raw values remain visible in the
  original telemetry and Diagnostics. A plot-only 20-km radius check prevents large
  horizontal corruptions from destroying the local track scale. The banner identifies
  omitted points; the flight state is not rewritten.
- The plot uses GPS offsets relative to the first FLIGHT position and the original
  reported barometric height, without assuming MSL/ellipsoid calibration. It is an
  observed local track; no synthetic simulation reference is overlaid.
- GS3 has no valid ground-receiver GPS. Automatic simulated tracking is unavailable
  there. Manual simulated pointer controls remain available. GS1/GS2 use recorded
  receiver GPS and the original UI's barometric-height pointing convention.
- Zero battery-cell values are retained. The recorded maximum barometric altitude
  is 5,549.227539 m. The files end in MAIN descent; no landing data is fabricated.
- Video remains the bundled FFmpeg test pattern and is labeled accordingly.
- Commands in DEMO are simulated; no physical serial worker is opened. User mission
  settings and the preceding reference survive a return to LIVE. LIVE remains default.

`demo.py` handles dataset integrity, receiver timing and the display frame;
`legacy_sample.py` safely decodes CSV literals without `eval`;
`controller.py` handles playback and source isolation. Tests cover all 14,769 rows,
source hashes, launch timing, gaps, seeking, end-of-recording, receiver switching,
legacy table values and recording provenance. Package checks run all three logs
with a minimal system PATH.
