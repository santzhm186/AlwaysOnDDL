"""Explicit entry point; --help works without Tk, Windows APIs, or network access."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "IronWill" if base else Path.home() / ".ironwill"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="ironwill",
        description="IronWill 桌面端。直接打开只显示界面；点击开始，或使用 run，才开始监督。",
    )
    parser.add_argument("command", nargs="?", choices=["run"], help="使用已保存目标开始监督；首次使用需先填写目标")
    parser.add_argument("--demo", action="store_true", help="安全体验：不读取窗口、不联网、不锁屏、不写个人配置")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="本地数据目录（默认 LOCALAPPDATA/IronWill）")
    parser.add_argument("--self-check", type=Path, metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_check:
        try:
            from .app import smoke_test
            result = smoke_test()
            # Verify the optional pairing bundle without opening any network socket.
            import ssl
            import tempfile
            from .relay import RelayServer
            with tempfile.TemporaryDirectory(prefix="ironwill-tls-check-") as directory:
                relay = RelayServer(Path(directory), host="127.0.0.1", port=0)
                certificate, private_key = relay._credentials()
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(certificate), str(private_key))
            result["tls_dependencies"] = True
            # Real current-user DPAPI check with a synthetic key, never the user's store.
            from .credentials import KeyStore
            with tempfile.TemporaryDirectory(prefix="ironwill-key-check-") as directory:
                store = KeyStore(Path(directory))
                store.save("https://self-check.invalid/v1", "SYNTHETIC-SELF-CHECK-KEY")
                if KeyStore(Path(directory)).load("https://self-check.invalid/v1") != "SYNTHETIC-SELF-CHECK-KEY":
                    raise RuntimeError("DPAPI round trip failed")
                if b"SYNTHETIC-SELF-CHECK-KEY" in store.path.read_bytes():
                    raise RuntimeError("DPAPI plaintext leak")
                store.delete()
            result["dpapi_storage"] = True
        except Exception as error:
            result = {"ok": False, "error_type": type(error).__name__, "error": str(error)}
        try:
            args.self_check.parent.mkdir(parents=True, exist_ok=True)
            args.self_check.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return 2
        return 0 if result.get("ok") else 1
    # Deliberately import the graphical application only after argument parsing.
    from .app import launch

    return launch(data_dir=args.data_dir, demo=args.demo, auto_start=args.command == "run")


if __name__ == "__main__":
    raise SystemExit(main())
