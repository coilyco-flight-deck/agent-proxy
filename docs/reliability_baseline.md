# Reliability measurement - M2 baseline-to-after

How the M2 reliability measurement is taken, what the harness emits, and how to
read the result without overclaiming.

The durable record for the M2 milestone number: the reliability harness
(`scripts/reliability_loop.py`, leg 05) run against both targets, scored by the
proxy's own `validate_response`, producing a reliability percentage and a
failure histogram for each. This file is the human record; the harness's
`--json` artifact is the machine-readable companion.

## Contents

- [Status and method](reliability-baseline-method.md)
- [Results and interpretation](reliability-baseline-results.md)
