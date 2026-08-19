#!/usr/bin/env python3
"""Safely copy a Markdown report into the active Obsidian Vault's 00-Inbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime


DEFAULT_CONFIG = Path.home() / "Library/Application Support/obsidian/obsidian.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_vault(config_path: Path) -> Path:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Obsidian config not found: {config_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read Obsidian config: {exc}") from exc

    vaults = data.get("vaults")
    if not isinstance(vaults, dict):
        raise RuntimeError("Obsidian config does not contain a vaults object")

    opened = []
    for item in vaults.values():
        if isinstance(item, dict) and item.get("open") is True and item.get("path"):
            opened.append(item)
    if not opened:
        raise RuntimeError("No active Obsidian Vault is marked open")

    selected = max(opened, key=lambda item: int(item.get("ts") or 0))
    vault = Path(str(selected["path"])).expanduser().resolve()
    if not vault.is_dir():
        raise RuntimeError(f"Active Obsidian Vault does not exist: {vault}")
    return vault


def safe_relative_dir(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError("Destination directory must be a safe Vault-relative path")
    return relative


def collision_path(destination: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = destination.with_name(f"{destination.stem}-更新-{timestamp}{destination.suffix}")
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}-{counter}{base.suffix}")
        counter += 1
    return candidate


def exclusive_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(destination, flags, 0o644)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb", closefd=False) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        finally:
            os.close(fd)
        raise
    os.close(fd)


def deliver(source: Path, config: Path, relative_dir: str) -> dict[str, str]:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"Source report does not exist: {source}")
    if source.suffix.lower() != ".md":
        raise RuntimeError("Only Markdown (.md) reports can be delivered")

    vault = active_vault(config.expanduser().resolve())
    inbox = (vault / safe_relative_dir(relative_dir)).resolve()
    try:
        inbox.relative_to(vault)
    except ValueError as exc:
        raise RuntimeError("Resolved destination escapes the active Vault") from exc

    source_hash = sha256(source)
    destination = inbox / source.name
    if destination.exists():
        if destination.is_file() and sha256(destination) == source_hash:
            return {
                "status": "already-present",
                "vault": str(vault),
                "destination": str(destination),
                "sha256": source_hash,
            }
        destination = collision_path(destination)

    exclusive_copy(source, destination)
    destination_hash = sha256(destination)
    if destination_hash != source_hash:
        destination.unlink(missing_ok=True)
        raise RuntimeError("SHA-256 verification failed; the newly created copy was removed")

    return {
        "status": "copied",
        "vault": str(vault),
        "destination": str(destination),
        "sha256": source_hash,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Markdown report to copy")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to obsidian.json")
    parser.add_argument("--relative-dir", default="00-Inbox", help="Vault-relative destination directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = deliver(args.source, args.config, args.relative_dir)
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
