"""Debounced, serial ingestion of completed or quiet source-file versions."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .ingestion import signature

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    """Only enqueue events; never parse or sleep in the observer thread."""

    def __init__(self, enqueue: Callable[[Path], None], extensions: tuple[str, ...]) -> None:
        """Bind a directory's supported extensions and enqueue callback."""
        self._enqueue = enqueue
        self._exts = extensions

    def _handle(self, path: str) -> None:
        """Queue supported basenames, leaving staging files untouched."""
        candidate = Path(path)
        if candidate.suffix.lower() in self._exts and not candidate.name.startswith('.'):
            self._enqueue(candidate)

    def on_created(self, event) -> None:
        """A creation starts the stability timer; it is not proof of completion."""
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event) -> None:
        """Restart the quiet interval whenever a producer writes more data."""
        if not event.is_directory:
            self._handle(event.src_path)

    def on_closed(self, event) -> None:
        """Handle write-close events on platforms that expose them."""
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:
        """Support the recommended transfer.part to transfer.csv publication."""
        if not event.is_directory:
            self._handle(event.dest_path)


class Watcher:
    """One worker serializes callbacks after each file's size/mtime stabilize."""

    def __init__(self, settle_seconds: float = 2.0, retry_delays=(1.0, 3.0, 10.0)) -> None:
        """Allow short test timings without altering production defaults."""
        self._observer = Observer()
        self._settle = settle_seconds
        self._retry_delays = retry_delays
        self._condition = threading.Condition()
        self._pending: dict[Path, dict] = {}
        self._watches: dict[Path, tuple] = {}
        self._errors: dict[str, str] = {}
        self._stopping = False
        self._running = False
        self._worker: threading.Thread | None = None

    def enqueue(self, path: Path, callback: Callable[[Path], object]) -> None:
        """Replace a pending version with the newest signature and quiet deadline."""
        path = Path(path)
        try:
            current = signature(path)
        except FileNotFoundError:
            return
        with self._condition:
            existing = self._pending.get(path)
            if existing and existing['signature'] == current:
                return
            self._pending[path] = {'signature': current, 'due': time.monotonic() + self._settle,
                                   'callback': callback, 'attempt': 0}
            self._errors.pop(str(path), None)
            self._condition.notify_all()

    def watch(self, directory: str | Path, callback: Callable[[Path], object],
              extensions: tuple[str, ...]) -> None:
        """Idempotently add an observer and enqueue existing directory files."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with self._condition:
            if directory in self._watches:
                return
        # Watchdog dispatches callbacks while holding its own lock. Never acquire
        # that lock while holding our condition, or a simultaneous event deadlocks.
        handler = _Handler(lambda p: self.enqueue(p, callback), extensions)
        watch = self._observer.schedule(handler, str(directory), recursive=False)
        with self._condition:
            self._watches[directory] = (watch, callback)
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in extensions:
                self.enqueue(path, callback)
        log.info('Watching %s for %s', directory, extensions)

    def unwatch(self, directory: str | Path) -> None:
        """Stop a source at runtime and remove its queued, unprocessed files."""
        directory = Path(directory)
        with self._condition:
            item = self._watches.pop(directory, None)
            self._pending = {p: task for p, task in self._pending.items()
                             if not p.is_relative_to(directory)}
            self._condition.notify_all()
        if item:
            self._observer.unschedule(item[0])

    def _run(self) -> None:
        """Process stable versions serially and retain failed-source errors."""
        while True:
            with self._condition:
                if self._stopping:
                    return
                if not self._pending:
                    self._condition.wait()
                    continue
                path, task = min(self._pending.items(), key=lambda item: item[1]['due'])
                delay = task['due'] - time.monotonic()
                if delay > 0:
                    self._condition.wait(delay)
                    continue
                del self._pending[path]
            try:
                if signature(path) != task['signature']:
                    self.enqueue(path, task['callback'])
                    continue
                task['callback'](path)
                with self._condition:
                    self._errors.pop(str(path), None)
            except FileNotFoundError:
                continue
            except Exception as error:
                log.exception('Import failed for %s', path.name)
                with self._condition:
                    self._errors[str(path)] = str(error)
                    if task['attempt'] < len(self._retry_delays) and path not in self._pending:
                        task['due'] = time.monotonic() + self._retry_delays[task['attempt']]
                        task['attempt'] += 1
                        self._pending[path] = task

    def status(self) -> dict:
        """Expose queue and failures, distinct from observer liveness."""
        with self._condition:
            return {'running': self._running and self._observer.is_alive()
                               and bool(self._worker and self._worker.is_alive()),
                    'pending': len(self._pending),
                    'errors': [{'file': Path(path).name, 'error': error}
                               for path, error in self._errors.items()]}

    def start(self) -> None:
        """Start the observer and the single ingestion worker."""
        if self._running:
            return
        self._stopping = False
        self._observer.start()
        self._worker = threading.Thread(target=self._run, name='obd-ingestion', daemon=True)
        self._running = True
        self._worker.start()

    def stop(self) -> None:
        """Wake idle workers and stop threads without an unbounded queue drain."""
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
        if self._worker:
            self._worker.join(timeout=5)
        self._running = False
