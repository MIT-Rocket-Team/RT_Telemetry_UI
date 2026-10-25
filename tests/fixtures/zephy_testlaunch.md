# Zephyrus simulation regression fixture

Derived from the user's:
`/Users/mdn/Developer/ActiveControl_MIT_RktTeam/sim/FCsim/benchmarking/ork/zephy_testlaunch.ork`

Original SHA-256:
`88e1e452fb3151fcce4d58e9775f34f17edaa86ada3c90fe2ecc30e2256d513a`

`zephy_testlaunch.ork` preserves the original rocket geometry, motor references,
saved configuration, launch conditions and extension declaration. Only stored
`flightdata` elements are removed and simulation status is set to `outdated`.
The original file is not modified. This keeps the fixture small and requires
the regression to generate a fresh flight rather than use cached results.

The integration test runs the bundled Java engine with no user-supplied motor
files and verifies resolution of the exact MITRT N8406 motor digest, a finite
trajectory, motor-file provenance and disabled simulation extensions. Additional
tests cover a missing curve and a configuration without an active motor.
The unmodified original model is also exercised with `verify_package.py --model`.
