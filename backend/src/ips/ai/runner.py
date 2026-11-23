"""Lifecycle wrapper for the API process; composition owns start and stop."""

from threading import Event, Thread


class AIRunner:
    def __init__(self, orchestrator, poll_seconds=1.0):
        self.orchestrator = orchestrator
        self.poll_seconds = poll_seconds
        self._stop = Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="ai-runner", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                worked = self.orchestrator.run_once()
            except Exception:  # noqa: BLE001 - keep the background worker alive.
                worked = False
            if not worked:
                self._stop.wait(self.poll_seconds)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)
