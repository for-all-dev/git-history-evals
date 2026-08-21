#!/usr/bin/env python3
import base64
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

# NOTE: the GitHub contents API is case- and spelling-sensitive on `path`. The first
# run of this script (candidates without the LICENCE/.MD variants below) missed two
# real license files that a manual follow-up audit found: NethermindEth/Clear's
# LICENSE.MD (uppercase extension) and ionathanch/TTBFL's LICENCE (British spelling).
# Both variants are included below so a re-run reproduces the audited result without
# manual patching.
CANDIDATE_PATHS = [
    "LICENSE",
    "LICENSE.md",
    "LICENSE.MD",
    "LICENSE.txt",
    "LICENSE-MIT",
    "LICENSE-APACHE",
    "LICENCE",
    "LICENCE.md",
    "LICENCE.txt",
    "COPYING",
    "COPYING.md",
    "COPYING.txt",
    "License",
    "license",
    "UNLICENSE",
]

FLAG_SUFFIX = " -- FLAG: needs manual review, may be non-redistributable"

MANUAL_OVERRIDES = {
    "Clear": (
        "LicenseRef-Nethermind-NonCommercial",
        "Nethermind custom license (non-SPDX, found at LICENSE.MD): grants copy/modify/"
        "distribute rights solely for Non-Commercial Use (research, personal study, "
        "education, nonprofit); commercial use, resale, and third-party "
        "verification-as-a-service are explicitly prohibited (clauses 3, 3.1, 3.2). "
        "-- FLAG: non-standard and non-commercial-only; likely needs a documented "
        "non-commercial-use caveat on the dataset card, a separate non-commercial-only "
        "export, or dropping from the mixed corpus -- needs an explicit human decision.",
    ),
}


def parse_owner_repo(url: str):
    clean_url = url.strip()
    clean_url = clean_url.removesuffix(".git")
    clean_url = clean_url.rstrip("/")
    parts = clean_url.split("/")
    return parts[-2], parts[-1]


def classify_license(text: str):
    text_lower = text.lower()

    # Dual Apache-2.0 OR MIT
    if "apache license" in text_lower and "mit license" in text_lower:
        return "Apache-2.0 OR MIT", "Dual Apache-2.0 / MIT License"

    # Apache-2.0
    if "apache license" in text_lower and (
        "2.0" in text_lower or "version 2.0" in text_lower
    ):
        return "Apache-2.0", "Apache License 2.0"

    # MIT
    if (
        "permission is hereby granted, free of charge" in text_lower
        or "mit license" in text_lower
    ):
        return "MIT", "MIT License"

    # AGPL-3.0
    if "affero general public license" in text_lower or (
        "affero" in text_lower and "general public license" in text_lower
    ):
        return "AGPL-3.0", "GNU Affero General Public License v3.0"

    # LGPL-2.1 / LGPL-3.0
    if (
        "lesser general public license" in text_lower
        or "library general public license" in text_lower
    ):
        if (
            "version 3" in text_lower
            or "v3" in text_lower
            or " 3.0" in text_lower
            or " 3" in text_lower
        ):
            return "LGPL-3.0", "GNU Lesser General Public License v3.0"
        elif "2.1" in text_lower or "version 2.1" in text_lower:
            return "LGPL-2.1", "GNU Lesser General Public License v2.1"
        return "LGPL-2.1", "GNU Lesser General Public License"

    # GPL-3.0 / GPL-2.0
    if "gnu general public license" in text_lower or (
        "general public license" in text_lower and "gnu" in text_lower
    ):
        if (
            "version 3" in text_lower
            or "v3" in text_lower
            or " 3.0" in text_lower
            or " 3" in text_lower
        ):
            return "GPL-3.0", "GNU General Public License v3.0"
        elif (
            "version 2" in text_lower
            or "v2" in text_lower
            or " 2.0" in text_lower
            or " 2" in text_lower
        ):
            return "GPL-2.0", "GNU General Public License v2.0"
        return "GPL-3.0", "GNU General Public License"

    # MPL-2.0
    if "mozilla public license" in text_lower and (
        "2.0" in text_lower or "version 2.0" in text_lower
    ):
        return "MPL-2.0", "Mozilla Public License 2.0"

    # BSD-3-Clause
    if (
        "redistribution and use in source and binary forms" in text_lower
        and "neither the name" in text_lower
    ):
        return "BSD-3-Clause", "BSD 3-Clause License"

    # BSD-2-Clause
    if (
        "redistribution and use in source and binary forms" in text_lower
        and "neither the name" not in text_lower
    ):
        return "BSD-2-Clause", "BSD 2-Clause License"

    # ISC
    if (
        "isc license" in text_lower
        or "permission to use, copy, modify, and/or distribute this software"
        in text_lower
        or "permission to use, copy, modify, and distribute this software" in text_lower
    ):
        return "ISC", "ISC License"

    # zlib License
    if (
        "zlib" in text_lower
        and "the origin of this software must not be misrepresented" in text_lower
    ):
        return "Zlib", "zlib License"

    # Unlicense
    if "this is free and unencumbered software" in text_lower:
        return "Unlicense", "The Unlicense"

    # CC0-1.0
    if "creative commons" in text_lower and (
        "cc0" in text_lower
        or "public domain" in text_lower
        or "universal" in text_lower
    ):
        return "CC0-1.0", "Creative Commons Zero v1.0 Universal"

    # CC-BY-NC-4.0
    if "creative commons" in text_lower and (
        "noncommercial" in text_lower or "by-nc" in text_lower
    ):
        return "CC-BY-NC-4.0", "Creative Commons Attribution NonCommercial 4.0"

    # CC-BY-4.0
    if "creative commons" in text_lower and (
        "attribution 4.0" in text_lower
        or "by 4.0" in text_lower
        or "by/4.0" in text_lower
    ):
        return "CC-BY-4.0", "Creative Commons Attribution 4.0 International"

    # Short proprietary check (<400 chars and "all rights reserved" with no grant language)
    grant_words = [
        "permission",
        "grant",
        "redistribute",
        "license",
        "copy",
        "modify",
        "free of charge",
    ]
    if (
        len(text) < 400
        and "all rights reserved" in text_lower
        and not any(g in text_lower for g in grant_words)
    ):
        return "NONE", "Short license text with All Rights Reserved"

    return "NOASSERTION", f"Unrecognized license file text ({len(text)} chars)"


def main():
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    repos_tsv_path = script_dir / "repos.tsv"
    licenses_tsv_path = script_dir / "licenses.tsv"

    if not repos_tsv_path.exists():
        repos_tsv_path = repo_root / "pipeline" / "repos.tsv"
        licenses_tsv_path = repo_root / "pipeline" / "licenses.tsv"

    with open(repos_tsv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    total_repos = len(rows)
    results = []

    for i, r in enumerate(rows, 1):
        name = r["name"]
        url = r["url"]
        revision = r["revision"]
        owner, repo = parse_owner_repo(url)

        found_path = None
        decoded_text = None

        for path in CANDIDATE_PATHS:
            endpoint = f"repos/{owner}/{repo}/contents/{path}?ref={revision}"
            cmd = ["gh", "api", endpoint]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode == 0:
                lines = proc.stdout.splitlines()
                filtered = "\n".join(
                    line for line in lines if not line.startswith("mise ")
                )
                try:
                    data = json.loads(filtered)
                    if isinstance(data, dict) and data.get("content"):
                        found_path = path
                        raw_bytes = base64.b64decode(data["content"])
                        decoded_text = raw_bytes.decode("utf-8", errors="replace")
                        break
                except Exception:  # noqa: BLE001, S110 -- try the next candidate path
                    pass

        if name in MANUAL_OVERRIDES:
            spdx, note = MANUAL_OVERRIDES[name]
            license_url = (
                f"https://github.com/{owner}/{repo}/blob/{revision}/{found_path}"
                if found_path
                else ""
            )
        elif found_path:
            spdx, note = classify_license(decoded_text)
            license_url = (
                f"https://github.com/{owner}/{repo}/blob/{revision}/{found_path}"
            )
        else:
            spdx = "NONE"
            license_url = ""
            note = (
                f"No LICENSE-like file found for {owner}/{repo} at revision {revision} "
                f"(tried paths: {', '.join(CANDIDATE_PATHS)})"
            )

        if spdx in ("NOASSERTION", "NONE") and not note.endswith(FLAG_SUFFIX):
            note += FLAG_SUFFIX

        results.append(
            {
                "name": name,
                "spdx": spdx,
                "license_url": license_url,
                "notes": note,
            }
        )

        sys.stderr.write(
            f"[{i}/{total_repos}] {name}: spdx={spdx}, path={found_path or 'NONE'}\n"
        )
        sys.stderr.flush()

    # Write pipeline/licenses.tsv
    with open(licenses_tsv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "spdx", "license_url", "notes"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(results)

    # Print summary to stdout
    print("\n--- LICENSE SURVEY SUMMARY ---")
    print(f"Total Repos Surveyed: {total_repos}")

    spdx_counts = Counter(r["spdx"] for r in results)
    print("\nSPDX License Counts:")
    for spdx_id, count in sorted(spdx_counts.items()):
        print(f"  {spdx_id}: {count}")

    flagged_repos = [r["name"] for r in results if r["spdx"] in ("NOASSERTION", "NONE")]
    print(f"\nFlagged Repositories (NOASSERTION or NONE): {len(flagged_repos)}")
    for repo_name in flagged_repos:
        print(f"  - {repo_name}")


if __name__ == "__main__":
    main()
