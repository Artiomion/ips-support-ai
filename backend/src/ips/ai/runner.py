"""Lifecycle wrapper for the API process; composition owns start and stop."""

from threading import Event, Thread, current_thread

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine


class AIRunner:
    def __init__(self, orchestrator, engine: Engine, poll_seconds=1.0):
        self.orchestrator = orchestrator
        self.engine = engine
        self.poll_seconds = poll_seconds
        self._stop = Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="ai-runner", daemon=True)
        event.listen(self.engine, "begin", self._bound_database_wait)
        self._thread.start()

    def _bound_database_wait(self, connection: Connection):
        # Search and quota can open their own sessions on the shared API engine.
        if current_thread() is self._thread:
            connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")

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
            self._thread.join()
            if event.contains(self.engine, "begin", self._bound_database_wait):
                event.remove(self.engine, "begin", self._bound_database_wait)
