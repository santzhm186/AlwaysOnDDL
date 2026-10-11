# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Build-time only: collect notices from the installed cryptography wheel's SBOM.

Downloads public crates, checks their exact SBOM SHA-256 and reads license files
without extracting archives. No new runtime dependency or user data access.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.metadata import distribution
import io
import json
from pathlib import Path
import re
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def collect(component):
    name, version = component["name"], component["version"]
    if not all(re.fullmatch(r"[A-Za-z0-9_.+-]+", value) for value in (name, version)):
        raise ValueError("Invalid crate identity")
    digest = next(h["content"] for h in component["hashes"] if h["alg"] == "SHA-256")
    url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=12) as response:
                raw = response.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Crate checksum or size mismatch: " + name)
            break
        except OSError:
            if attempt:
                raise
    notices = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive.getmembers():
            base = Path(member.name).name.casefold()
            if member.isfile() and base.startswith(("license", "licence", "copying", "notice", "copyright")):
                if member.size > 256 * 1024:
                    raise ValueError("License unexpectedly oversized: " + name)
                with archive.extractfile(member) as stream:
                    notices.append((member.name, stream.read().decode("utf-8")))
    if not notices:
        raise ValueError("No license files in crate: " + name)
    expression = component["licenses"][0]["expression"]
    if "Apache-2.0 WITH LLVM-exception" in expression:
        selected = "Apache-2.0 WITH LLVM-exception"
    elif "Apache-2.0" in expression:
        selected = "Apache-2.0" + (" AND Unicode-3.0" if "Unicode-3.0" in expression else "")
    elif expression in {"MIT", "BSD-3-Clause"}:
        selected = expression
    else:
        raise ValueError("License expression requires manual review: " + name)
    record = {"name": name, "version": version, "license_expression": expression,
              "selected_license": selected, "crate_url": url, "sha256": digest,
              "notice_files": [name for name, _ in sorted(notices)]}
    text = f"\n===== {name} {version} =====\nDeclared: {expression}\nSelected: {selected}\nSource: {url}\nSHA-256: {digest}\n"
    for path, content in sorted(notices):
        text += f"\n--- {path} ---\n{content}\n"
    return record, text


def main():
    installed = distribution("cryptography")
    sbom_path = next(path for path in installed.files if str(path).endswith("sboms/cryptography-rust.cyclonedx.json"))
    sbom = json.loads(installed.locate_file(sbom_path).read_text(encoding="utf-8"))
    external = sorted((c for c in sbom["components"] if c.get("purl", "").startswith("pkg:cargo/")
                       and not c["name"].startswith("cryptography-")), key=lambda c: c["name"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(collect, external))
    inventory = {"cryptography_version": installed.version, "source": "Installed wheel CycloneDX SBOM",
                 "sbom_sha256": hashlib.sha256(installed.locate_file(sbom_path).read_bytes()).hexdigest(),
                 "scope": "All external Rust crates declared by this wheel, including build/proc-macro crates",
                 "components": [record for record, _ in results]}
    output = ROOT / "licenses"
    # Write only after every archive validates; incomplete runs leave old output intact.
    (output / "cryptography-rust-components.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    heading = ("Third-party Rust notices for cryptography " + installed.version + "\n"
               "Original third-party copyrights and licenses are retained below.\n"
               "Each crate archive was checked against the installed wheel SBOM SHA-256.\n"
               "For dual licenses we select the recorded permissive option; other texts\n"
               "may remain as upstream alternatives and do not relicense IronWill.\n"
               "Internal cryptography crates use the separately included BSD license.\n")
    (output / "Rust-THIRD-PARTY.txt").write_text(heading + "".join(text for _, text in results), encoding="utf-8")
    print(f"Collected and checksum-verified {len(results)} external Rust crates for cryptography {installed.version}.")


if __name__ == "__main__":
    main()
