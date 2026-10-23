"""Offline reviewer tool. Never place the private review key inside a flow or repository."""

import argparse
import json
from pathlib import Path
from lfx.components.enterprise_delivery.contracts import issue_grant

parser = argparse.ArgumentParser()
parser.add_argument("--proposal", type=Path, required=True)
parser.add_argument("--reviewer", required=True)
parser.add_argument("--private-key", type=Path, required=True)
args = parser.parse_args()
package = json.loads(args.proposal.read_text(encoding="utf-8"))
print(issue_grant(package["change"], args.reviewer, args.private_key.read_bytes()))
