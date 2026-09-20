"""Copy legacy upload bytes to S3 and verify read-back; never remove originals."""

import argparse
import hashlib
from pathlib import Path

from src.app.config import get_settings
from src.app.integrations.object_storage import get_object, put_object
from src.app.services.knowledge import MAX_KNOWLEDGE_BYTES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    count = 0
    for supplied_root in args.roots:
        root = supplied_root.resolve(strict=True)
        for source in sorted((root / "knowledge").rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            if root not in source.resolve().parents:
                raise ValueError("Upload resolves outside the supplied root")
            if source.stat().st_size > MAX_KNOWLEDGE_BYTES:
                raise ValueError("Legacy upload exceeds the supported size")
            key = source.relative_to(root).as_posix()
            content = source.read_bytes()
            # Legacy keys contain the source hash; reject unexpected/corrupt bytes.
            if source.name.split("_", 1)[0] != hashlib.sha256(content).hexdigest():
                raise ValueError("Legacy upload does not match its content-addressed key")
            put_object(settings, key, content)
            if get_object(settings, key, max_bytes=MAX_KNOWLEDGE_BYTES) != content:
                raise ValueError("S3 read-back does not match the legacy file")
            count += 1
    print(f"Copied and verified {count} files. Original files were retained.")


if __name__ == "__main__":
    main()
