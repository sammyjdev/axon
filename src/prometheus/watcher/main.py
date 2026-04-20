from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".java", ".py", ".ts"}


def _is_supported(path: str) -> bool:
    return Path(path).suffix in _SUPPORTED_EXTENSIONS


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue[Path], loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def _enqueue(self, path: str) -> None:
        if _is_supported(path):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, Path(path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.dest_path)


@dataclass
class VaultWatcher:
    vault_path: Path
    queue: asyncio.Queue[Path] = field(default_factory=asyncio.Queue)
    _observer: Observer | None = field(default=None, init=False, repr=False)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        handler = _Handler(self.queue, loop)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.vault_path), recursive=True)
        self._observer.start()
        logger.info("Watcher started on %s", self.vault_path)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            logger.info("Watcher stopped")


async def run_watcher(vault_path: Path, on_file: asyncio.Coroutine) -> None:
    """Starts the watcher and calls on_file(path) for every changed file."""
    loop = asyncio.get_running_loop()
    watcher = VaultWatcher(vault_path=vault_path)
    watcher.start(loop)
    try:
        while True:
            path = await watcher.queue.get()
            try:
                await on_file(path)
            except Exception:
                logger.exception("Error processing %s", path)
    finally:
        watcher.stop()
