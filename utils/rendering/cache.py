import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from weakref import WeakValueDictionary
from utils.paths import shared_cache
from utils.json_io import atomic_write_bytes, atomic_write_json
from .models import RenderedDocument, RenderedPage

_locks = WeakValueDictionary()


async def cached_render(key: str, render, *, force=False):
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        directory = shared_cache("rendering") / key
        manifest = directory / "pages.json"
        if not force:
            try:
                items = json.loads(manifest.read_text())
                pages = []
                for i, item in enumerate(items):
                    data = (directory / f"{i}.png").read_bytes()
                    if hashlib.sha256(data).hexdigest() != item["sha256"]:
                        raise ValueError("Invalid cached page")
                    pages.append(RenderedPage(data, item["width"], item["height"]))
                if pages:
                    manifest.touch()
                    return RenderedDocument(tuple(pages))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        result = await render()
        if not result.pages:
            raise ValueError("Renderer produced no pages")
        # Publish the manifest last: readers never accept a partial document.
        manifest.unlink(missing_ok=True)
        records = []
        for i, page in enumerate(result.pages):
            await asyncio.to_thread(
                atomic_write_bytes, directory / f"{i}.png", page.data
            )
            records.append(
                {
                    "width": page.width,
                    "height": page.height,
                    "sha256": hashlib.sha256(page.data).hexdigest(),
                }
            )
        await asyncio.to_thread(atomic_write_json, manifest, records)
        return result


def cleanup_render_cache(now=None):
    root = shared_cache("rendering")
    if not root.exists():
        return
    cutoff = (time.time() if now is None else now) - 7 * 86400
    for directory in root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{64}", directory.name) or directory.is_symlink():
            continue
        lock = _locks.get(directory.name)
        if lock and lock.locked():
            continue
        manifest = directory / "pages.json"
        if manifest.is_file() and manifest.stat().st_mtime < cutoff:
            shutil.rmtree(directory)
            _locks.pop(directory.name, None)
