# Bundled team motor curves

`TestLaunch_v14.eng` is an unmodified copy of the MITRT N8406 curve from:
`/Users/mdn/Developer/ActiveControl_MIT_RktTeam/sim/old/dat/ork/TestLaunch_v14.eng`.

Its OpenRocket motor digest is `057a820844b93fe3307401623a92d4c6`, an exact match
to the motor reference in the user's `benchmarking/ork/zephy_testlaunch.ork`.
The `.ork` stores the motor's identity, not its thrust-curve samples. Loading
the model without this custom curve previously produced a zero-length flight.

The monitor loads these curves alongside the standard OpenRocket database for
each isolated job. The production machine does not need the original curve file,
a separate OpenRocket installation, or OpenRocket GUI preferences. Additional
user-selected `.eng`/`.rse` files remain supported.

`manifest.json` records provenance, the original byte hash and engine motor digest.
The app verifies the bundled bytes and copies every supplied curve into the job
folder; the trajectory manifest records input hashes and resolved motor digests.

Only a nominal simulation is run: saved Java extensions and the fork's global
inertia override remain disabled, and the mission's launch/wind settings apply.
