"""BlobStore：不可变内容寻址大对象存储（基础设施层 Layer 0）。"""

import hashlib
import os
import tempfile
from pathlib import Path


class LocalBlobStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, uri: str) -> Path:
        if not isinstance(uri, str) or not uri.startswith("blob://"):
            raise ValueError("Invalid blob URI")
        digest = uri[len("blob://") :]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Invalid blob URI")
        return self.root / digest[:2] / digest

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        target = self.root / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{digest[:8]}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                os.replace(tmp_name, target)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        return f"blob://{digest}"

    def get(self, uri: str) -> bytes:
        return self._resolve_path(uri).read_bytes()

    def delete(self, uri: str) -> None:
        self._resolve_path(uri).unlink(missing_ok=True)
