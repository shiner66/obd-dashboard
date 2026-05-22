"""Watchdog file watcher — auto-processes new CSV/.myop files dropped in watched dirs."""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent
from watchdog.observers import Observer

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Path], None], extensions: tuple[str, ...]) -> None:
        self._cb = callback
        self._exts = extensions

    def _handle(self, path: str) -> None:
        p = Path(path)
        if p.suffix.lower() in self._exts:
            log.info("Watcher detected new file: %s", p)
            try:
                self._cb(p)
            except Exception:
                log.exception("Error processing %s", p)

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self._handle(event.dest_path)


class Watcher:
    def __init__(self) -> None:
        self._observer = Observer()

    def watch(
        self,
        directory: str | Path,
        callback: Callable[[Path], None],
        extensions: tuple[str, ...],
    ) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        handler = _Handler(callback, extensions)
        self._observer.schedule(handler, str(directory), recursive=False)
        log.info("Watching %s for %s", directory, extensions)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
