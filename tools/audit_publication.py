# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Targeted publication checks; output locations only, never matched secrets.

This is not a comprehensive secret detector or a substitute for manual review.
Run from a Git checkout: python tools/audit_publication.py --history
"""
import argparse
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "provider_token": re.compile(rb"\b(?:sk-(?:proj-|ant-api\d{2}-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16})\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{40,}"),
    "credential_url": re.compile(rb"https?://[^\s/\"'<>:@]+:[^\s/\"'<>@]+@"),
    "literal_secret": re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[\"']([^\"'\r\n]{20,})[\"']"),
    "personal_path": re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+", re.I),
}
PRIVATE_NAMES = {"ui_settings.json", "policy.json", "session.json", "ai_judgments.json",
                 "iron_will_config.json", "token.txt", "deepseek_key.txt", "local.properties"}
PRIVATE_SUFFIXES = {".dpapi", ".key", ".crt", ".jks", ".keystore", ".pem", ".p12", ".pfx", ".jsonl"}


def git(*args, input=None):
    return subprocess.check_output(["git", *args], cwd=ROOT, input=input)


def scan(data, path, scope):
    findings = []
    name = Path(path).name
    parts = Path(path).parts
    if (name in PRIVATE_NAMES or Path(name).suffix.lower() in PRIVATE_SUFFIXES
            or name == ".env" or name.startswith(".env.") and name != ".env.example"
            or any(part in {"pairing", "secrets", "logs"} for part in parts)):
        findings.append({"scope": scope, "path": path, "category": "private_file"})
    for category, pattern in PATTERNS.items():
        for match in pattern.finditer(data):
            # This single synthetic URL proves the endpoint rejects URL credentials.
            if (category == "credential_url" and path == "desktop/tests/test_credentials.py"
                    and match.group() == b"https://user:" + b"password@"):
                continue
            findings.append({"scope": scope, "path": path,
                             "line": data[:match.start()].count(b"\n") + 1, "category": category})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Also scan blobs reachable from all local Git refs")
    args = parser.parse_args()
    findings = []
    paths = list(filter(None, git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")))
    for name in paths:
        path = ROOT / name
        if path.is_symlink():
            findings.append({"scope": "current", "path": name, "category": "symlink_requires_review"})
        elif path.is_file():
            findings.extend(scan(path.read_bytes(), name, "current"))
    blobs = 0
    if args.history:
        for entry in git("rev-list", "--objects", "--all").decode().splitlines():
            oid, _, name = entry.partition(" ")
            if git("cat-file", "-t", oid).strip() == b"blob":
                blobs += 1
                findings.extend(scan(git("cat-file", "blob", oid), name, "history"))
    license_data = (ROOT / "LICENSE").read_bytes()
    if license_data != (ROOT / "android/app/src/main/assets/LICENSE").read_bytes():
        findings.append({"scope": "current", "category": "android_license_differs"})
    if b"GNU GENERAL PUBLIC LICENSE" not in license_data or b"Version 3, 29 June 2007" not in license_data:
        findings.append({"scope": "current", "category": "gplv3_missing"})
    inventory = json.loads((ROOT / "licenses/cryptography-rust-components.json").read_text(encoding="utf-8"))
    requirements = (ROOT / "desktop/requirements.txt").read_text(encoding="utf-8").splitlines()
    if "cryptography==" + inventory["cryptography_version"] not in requirements:
        findings.append({"scope": "current", "category": "rust_notices_version_mismatch"})
    notices = (ROOT / "licenses/Rust-THIRD-PARTY.txt").read_text(encoding="utf-8")
    for component in inventory["components"]:
        if component["name"] + " " + component["version"] not in notices or component["sha256"] not in notices:
            findings.append({"scope": "current", "category": "rust_notice_missing", "component": component["name"]})
    probes = [".env", "ui_settings.json", "policy.json", "session.json", "ai_judgments.json",
              "pairing/token.txt", "logs/activity.jsonl", "secrets/ai-key.dpapi", "release.pem", "release.pfx"]
    ignored = set(git("check-ignore", "--stdin", input=("\n".join(probes) + "\n").encode()).decode().splitlines())
    for path in set(probes) - ignored:
        findings.append({"scope": "current", "path": path, "category": "ignore_rule_missing"})
    emails = git("log", "--all", "--format=%ae").decode().splitlines() if args.history else []
    print(json.dumps({"current_files": len(paths), "history_blobs": blobs,
                      "commits_with_non_noreply_email": sum("noreply" not in email for email in emails),
                      "findings": findings,
                      "limits": "Targeted patterns only; Git email metadata is reported, not rewritten."}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
