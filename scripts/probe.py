"""Run the fault and anomaly calibration probes against data/. Equivalent of `make probe`."""

from __future__ import annotations

import subprocess
import sys

PROBES = ["synth.probes.fault_probe", "synth.probes.calibration_probe"]


def main() -> int:
    status = 0
    for module in PROBES:
        print(f"$ python -m {module}", flush=True)
        status |= subprocess.call([sys.executable, "-m", module])
    return status


if __name__ == "__main__":
    raise SystemExit(main())
