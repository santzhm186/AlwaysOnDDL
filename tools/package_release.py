"""Package only the new project's source and documents; never include user data."""
from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_VERSION = "0.1.2"
SKIP_PARTS = {"build", "dist", "__pycache__", ".git", ".gradle", ".idea", ".venv", ".venv-build", "secrets", "pairing"}
SUFFIXES = {".py", ".java", ".md", ".txt", ".ps1", ".xml", ".gradle", ".properties"}


def source_files():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.name in {"token.txt", "local.properties", "deepseek_key.txt"}:
            continue
        if path.suffix.lower() in {".dpapi", ".key", ".crt", ".jks", ".keystore"}:
            continue
        if path.suffix.lower() in SUFFIXES or path.name == ".gitignore" or relative.parts[0] == "licenses":
            yield path, relative


def main():
    target = ROOT / "dist"
    target.mkdir(exist_ok=True)
    archive = target / f"IronWill-Source-{SOURCE_VERSION}.zip"
    selected = list(source_files())
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=9) as output:
        for path, relative in selected:
            output.write(path, str(Path("ironwill") / relative))
    with ZipFile(archive) as checked:
        assert checked.testzip() is None
        assert all(not name.endswith((".dpapi", ".key", ".crt", ".jks", ".keystore", "token.txt", "deepseek_key.txt")) for name in checked.namelist())
    names = ("IronWill.exe", f"IronWill-Windows-{SOURCE_VERSION}.exe", "IronWill-Android-0.1.apk", archive.name)
    lines = []
    for name in names:
        artifact = target / name
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
        print(f"{name}: {artifact.stat().st_size} bytes; SHA256 {digest}")
    (target / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"Source archive verified: {len(selected)} files; no original private config or signing key.")


if __name__ == "__main__":
    main()
