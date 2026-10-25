# Test-launch simulation regression fixture

Derived from the user's test-launch model under:
`/Users/mdn/Developer/ActiveControl_MIT_RktTeam/sim/FCsim/benchmarking/ork/`

Original SHA-256:
`88e1e452fb3151fcce4d58e9775f34f17edaa86ada3c90fe2ecc30e2256d513a`

`test_launch.ork` preserves the original rocket geometry, motor references,
saved configuration, launch conditions and extension declaration. Stored
`flightdata` elements are removed and simulation status is set to `outdated`.
Three component display names are neutralized to Actuator Housing, Actuator
Module and Actuator Set (4). Required external file-format element names and all
physical component values remain unchanged.
The original file is not modified. This keeps the fixture small and requires
the regression to generate a fresh flight rather than use cached results.

The integration test runs the bundled Java engine with no user-supplied motor
files and verifies resolution of the exact MITRT N8406 motor digest, a finite
trajectory, motor-file provenance and disabled simulation extensions. Additional
tests cover a missing curve and a configuration without an active motor.
The package verifier accepts this fixture or another user-selected model through
`verify_package.py --model`.

Slim fixture SHA-256 before display-name cleanup:
`13ab9cd90ec4ce01232d72b982904eb40b2a71c0dfa4ac8efa927f2f174d4b98`

Current fixture SHA-256:
`d52537ffcda5135ddb04683e907f9ac7301899b83fbd1811b70ccb22e2efd6ac`
