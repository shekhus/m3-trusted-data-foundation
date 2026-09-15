"""
Company-name collision check.

Run this BEFORE publishing the repo, and again if you change COMPANY_NAME.

Why this exists: plausible-sounding names in food and agriculture are heavily
occupied. The name is therefore a single config constant and this check is part
of the release routine rather than a one-off judgement call.

KNOWN ADJACENCY, reviewed and accepted: "Prairie Foods" (prairiefoods.farm) is a
real direct-to-consumer regenerative farm business in Indiana, PA. The dataset's
fictional "Prairie Bend Foods" is a distinct name describing a different kind of
business (a multi-plant industrial protein processor). Every generated artifact
states its fictional status explicitly and makes no claim about any real
company. This was a deliberate decision, not an oversight. Revisit it if the
portfolio is ever used commercially.

This script does not have network access in every environment, so it prints the
searches to run rather than running them. Verify by hand; it takes two minutes
and removes a real legal and reputational risk.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from synth import config as C  # noqa: E402

QUERIES = [
    '"{name}"',
    '"{name}" company',
    '"{short}" foods',
    '"{short}" meat OR beef OR protein',
    '"{short}" LLC OR Inc OR Ltd',
]


def main() -> int:
    name = C.COMPANY_NAME
    short = name.split()[0]
    print(f"Current COMPANY_NAME: {name!r}\n")
    print("Run each of these and confirm no company in food, agriculture, or")
    print("manufacturing appears. A hit in an unrelated sector (a school, a")
    print("consultancy) is acceptable; a hit in food is not.\n")
    for q in QUERIES:
        print("  " + q.format(name=name, short=short))
    print("\nAlso check: companieshouse.gov.uk, opencorporates.com, and a")
    print("trademark search in your target market.\n")
    print("If you change the name, change it ONLY in synth/config.py")
    print("(COMPANY_NAME plus the plant and warehouse names), then re-run:")
    print("  make synth && make test")
    print("\nThe test suite has a guard (test_no_real_company_names_in_output)")
    print("that fails if a banned string leaks into generated artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
