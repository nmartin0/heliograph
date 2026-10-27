# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine's own process: a JSON API over HTTP (decisions D18, D19, D37).

    heliograph serve --data ~/heliograph-data --configs examples

Every front end (the graphical workspace, scripts, tests) reads and acts
through this API; it holds no logic a front end needs to repeat. The
process:

- listens on ``127.0.0.1`` (``--host`` to change it) at ``--port``, 8765
  by default (0: any free port), and logs a ``listening`` event naming it;
- keeps its runs under ``--data``: each backtest's journal is
  ``runs/<id>.sqlite``, its id the configuration's name and the UTC time
  it was submitted;
- offers the run configurations (``*.toml``) in ``--configs``, if given;
- runs one backtest at a time, in the order submitted (one worker);
- logs one JSON object per line to stderr for each event;
- on SIGTERM or SIGINT tells the running backtest to stop at its next
  input (its journal is closed, marked as stopped), drops the queued ones,
  stops taking requests, and exits 0 within ``STOP_DEADLINE`` seconds; a
  backtest that has not stopped by then (blocked reading a file, say) is
  left behind, its journal marked running (read later as interrupted),
  and the process exits 1.

Every option can also come from the environment: ``HELIOGRAPH_HOST``,
``HELIOGRAPH_PORT``, ``HELIOGRAPH_DATA``, ``HELIOGRAPH_CONFIGS``. Relative
paths in a configuration (a data file's, say) are relative to the folder
the process was started in, as for the ``heliograph`` command.

The API, every body JSON, every time an ISO 8601 UTC string (D19):

    GET  /api/health                          status, version, what is running
    GET  /api/plugins                         installed plug-ins, by kind and name
    GET  /api/plugins/{kind}/{name}           one plug-in, with its parameters' JSON Schema
    GET  /api/configs                         the run configurations offered
    GET  /api/configs/{name}                  one, with its text
    POST /api/backtests                       {"config": TOML text} or {"config_name": name}
    GET  /api/backtests                       every backtest, the latest submitted first
    GET  /api/backtests/{id}                  one backtest's status and span; while it runs,
                                              how far it has gone (``at``, ``progress``)
    GET  /api/backtests/{id}/report           its report: figures per strategy, and as text
    GET  /api/backtests/{id}/contents         what can be charted: streams, nodes, strategies
    GET  /api/backtests/{id}/bars?stream=     the bars of a stream (``btc@1h``)
    GET  /api/backtests/{id}/nodes/{name}     an indicator's, forecaster's or gate's values
    GET  /api/backtests/{id}/signals          the signals sent and refused (``?strategy=``)
    GET  /api/backtests/{id}/fills            the simulated fills (``?strategy=``)
    GET  /api/backtests/{id}/equity/{name}    a strategy's equity, as the report counts it

The chart series (app/charts.py, D38) take ``?start=`` and ``?end=`` (ISO
8601: what became known in between), answer ``{"id", "status", "items"}``,
and can be read while their backtest runs, as far as it has gone.

An error is ``{"error": message}``: 400 for a request or configuration
that does not validate, 404 for something that is not there, 405 for a
method a path does not take, 409 for a report of a backtest that has none
(yet), and 500, logged with its traceback, for a bug.
"""

import asyncio
import contextlib
import json
import logging
import math
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from heliograph import __version__
from heliograph.app import charts
from heliograph.app.charts import NotInRunError
from heliograph.app.config import ConfigError, LoadedConfig, inputs_rule, parse_config
from heliograph.app.mounts import KIND as SOURCES
from heliograph.app.report import figures, report
from heliograph.app.run import StopFlag, run_backtest
from heliograph.core.clock import Clock, WallClock
from heliograph.core.errors import HeliographError
from heliograph.core.time import Timestamp, from_datetime, from_iso, to_datetime, to_iso
from heliograph.plugins.registry import Registry, installed_kinds
from heliograph.plugins.sqlite_journal import read_header, tidy

STOP_DEADLINE = 10.0
"""Seconds from a stop signal to the process's exit."""

CLOSING = 3
"""Seconds of those that open requests are given to finish."""

DEFAULT_PORT = 8765

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
"""What a backtest id or a configuration's name may be: a plain file name."""

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
"""What a backtest's name may be: short enough that its id (with the time) is one."""

_SUBMITTED = re.compile(r"-(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<number>\d+))?$")
"""The time a backtest was submitted, and its number within that second, in its id."""

_WAKE = object()
"""Put on the queue to wake the worker, so it sees it is to stop."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Where the process listens and keeps its runs."""

    data: Path
    configs: Path | None = None
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT


# -- logging ---------------------------------------------------------------------------


class _JsonLines(logging.Formatter):
    """One JSON object per event: its time, level and name, and what it carries."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self._clock = clock

    def format(self, record: logging.LogRecord) -> str:
        name, _, detail = record.getMessage().partition("\n")
        event: dict[str, Any] = {
            "time": to_iso(self._clock.now()),
            "level": record.levelname.lower(),
            "event": name,
        }
        if detail:  # a message of many lines (uvicorn's tracebacks): its name, then the rest
            event["detail"] = detail
        event.update(getattr(record, "fields", {}))
        if record.exc_info:
            event["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(event, default=str)


_log = logging.getLogger("heliograph.server")


def _event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    _log.log(level, name, extra={"fields": fields})


def _logging(clock: Clock) -> None:
    """Every log line to stderr as JSON: ours, and uvicorn's own."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonLines(clock))
    for name in ("heliograph.server", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False


# -- the backtests ---------------------------------------------------------------------


@dataclass(slots=True)
class _Job:
    """A backtest submitted to this process."""

    id: str
    loaded: LoadedConfig
    journal: Path
    order: int = 0  # submitted after every job with a smaller one
    status: str = "queued"
    error: str | None = None
    at: int | None = None  # the time of the latest input delivered, while it runs

    def seen(self, ts: Timestamp) -> None:
        self.at = ts  # one attribute, read without the lock: at worst a moment old

    def progress(self) -> dict[str, Any]:
        """How far the run has gone through its span, while it runs."""
        mounts = self.loaded.config.mounts.values()
        starts = [from_datetime(m.start) for m in mounts if m.start is not None]
        ends = [from_datetime(m.end) for m in mounts if m.end is not None]
        if self.at is None or not starts or not ends or max(ends) <= min(starts):
            return {}
        done = (self.at - min(starts)) / (max(ends) - min(starts))
        return {"at": to_iso(Timestamp(self.at)), "progress": round(min(max(done, 0.0), 1.0), 4)}


@dataclass(slots=True)
class Backtests:
    """The backtests under one data folder, and the one worker that runs them."""

    runs: Path
    clock: Clock
    _queue: "queue.Queue[_Job | object]" = field(default_factory=queue.Queue)
    _jobs: dict[str, _Job] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: StopFlag = field(default_factory=StopFlag)
    _stopped: bool = False
    _worker: threading.Thread | None = None

    def start(self) -> None:
        """Start the worker.

        Raises:
            ConfigError: if the runs folder cannot be made.
        """
        try:
            self.runs.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"cannot keep runs in {self.runs}: {exc.strerror or exc}") from exc
        # Journals an earlier engine left beside their -wal file (read as they closed,
        # or stopped mid-tidy) become single files; nothing else has them open yet.
        for wal in self.runs.glob("*.sqlite-wal"):
            with contextlib.suppress(Exception):
                tidy(wal.with_name(wal.name.removesuffix("-wal")), timeout=0.1)
        # A daemon: a backtest that will not stop by the deadline must not keep the
        # process from exiting (its journal is then left "running": interrupted).
        self._worker = threading.Thread(target=self._work, name="backtests", daemon=True)
        self._worker.start()

    def signal_stop(self) -> None:
        """Tell the running backtest to stop at its next input: safe in a signal
        handler, since it takes no lock. ``stop`` then does the rest."""
        self._stop.set()

    def stop(self) -> None:
        """Tell the running backtest to stop at its next input; drop the queued ones.
        Returns at once; ``wait`` waits for the worker. Not for a signal handler."""
        self._stop.set()
        if self._stopped:
            return
        self._stopped = True
        dropped = []
        with self._lock:
            for job in self._jobs.values():
                if job.status == "queued":
                    job.status = "dropped"
                    dropped.append(job.id)
        if dropped:
            _event("backtests dropped", ids=dropped)
        self._queue.put(_WAKE)

    def wait(self, timeout: float) -> bool:
        """Whether the worker has finished, waiting up to ``timeout`` seconds."""
        if self._worker is None:
            return True
        self._worker.join(max(timeout, 0.0))
        return not self._worker.is_alive()

    def submit(self, name: str, loaded: LoadedConfig) -> _Job:
        """Queue a backtest of ``loaded``; its id is ``name`` and the time, made unique."""
        base = f"{name}-{to_datetime(self.clock.now()):%Y%m%dT%H%M%SZ}"
        with self._lock:
            id_, number = base, 1
            while id_ in self._jobs or (self.runs / f"{id_}.sqlite").exists():
                number += 1
                id_ = f"{base}-{number}"
            job = _Job(id_, loaded, self.runs / f"{id_}.sqlite", order=len(self._jobs) + 1)
            self._jobs[id_] = job
        self._queue.put(job)
        _event("backtest queued", id=id_)
        return job

    def _work(self) -> None:
        while not self._stop.is_set():
            job = self._queue.get()
            if not isinstance(job, _Job) or self._stop.is_set():
                continue
            with self._lock:
                if job.status != "queued":
                    continue
                job.status = "running"
            _event("backtest started", id=job.id)
            # Done before it is logged: a client told by the log reads it done.
            try:
                summary = run_backtest(
                    job.loaded,
                    _registry(job.loaded),
                    job.journal,
                    self.clock,
                    asyncio.sleep,
                    stop=self._stop,
                    progress=job.seen,
                )
            except HeliographError as exc:
                self._done(job, str(exc))
                _event("backtest failed", logging.WARNING, id=job.id, error=job.error)
            except Exception as exc:  # a bug: logged with its traceback, the worker goes on
                self._done(job, f"{type(exc).__name__}: {exc}")
                _log.exception("backtest crashed", extra={"fields": {"id": job.id}})
            else:
                self._done(job, None)
                _event("backtest finished", id=job.id, inputs=summary.delivered)

    def _done(self, job: _Job, error: str | None) -> None:
        # A chart read as the run closed may have kept its journal from becoming one
        # file; it is tried again now, before the run is said to be done. Not while
        # the process stops: its deadline comes first, and the journal is whole as it
        # is (the next engine on this folder tidies it).
        if not self._stop.is_set():
            with contextlib.suppress(Exception):
                tidy(job.journal)
        with self._lock:
            job.error = error
            job.status = "done"

    def running(self) -> str | None:
        """The id of the backtest running now, if one is."""
        with self._lock:
            return next((j.id for j in self._jobs.values() if j.status == "running"), None)

    def ids(self) -> list[str]:
        """Every backtest's id, the latest submitted first: the journals here, and
        those not yet run. Ids that do not say when (a file put there by hand) last."""
        with self._lock:
            orders = {id_: job.order for id_, job in self._jobs.items()}
        known = set(orders) | {
            path.stem for path in self.runs.glob("*.sqlite") if _ID.match(path.stem)
        }

        def latest_first(id_: str) -> tuple[str, int, int, str]:
            found = _SUBMITTED.search(id_)
            if found is None:
                return ("", 0, 0, id_)
            return (found["stamp"], orders.get(id_, 0), int(found["number"] or 1), id_)

        return sorted(known, key=latest_first, reverse=True)

    def describe(self, id_: str) -> dict[str, Any] | None:
        """A backtest's id, status and span, or None if there is none by that id."""
        if not _ID.match(id_):
            return None
        with self._lock:
            job = self._jobs.get(id_)
            status = job.status if job is not None else None
            error = job.error if job is not None else None
        journal = self.runs / f"{id_}.sqlite"
        if status == "running" and job is not None:
            # Its journal may not exist yet, or be half written.
            return {"id": id_, "status": status, **job.progress()}
        if status in ("queued", "dropped"):
            return {"id": id_, "status": status}
        if not journal.is_file():
            if status is None:
                return None
            # Refused before its journal was written: a mount it could not serve, say.
            return {"id": id_, "status": f"failed: {error}"}
        header = _header(journal)
        recorded = header.get("status") or "unreadable: not a backtest's journal"
        if recorded == "running":
            recorded = "interrupted"  # the process that ran it ended before it did
        described: dict[str, Any] = {"id": id_, "status": recorded}
        for key in ("start", "end"):
            if header.get(key, "").lstrip("-").isdigit():
                described[key] = to_iso(Timestamp(int(header[key])))
        if "config_digest" in header:
            described["config_digest"] = header["config_digest"]
        return described


def _header(journal: Path) -> dict[str, str]:
    try:
        return read_header(journal)
    except Exception:  # a file that is not a journal, or one being created
        return {}


def _registry(loaded: LoadedConfig) -> Registry:
    """The installed plug-ins of the kinds a configuration uses, as for the command."""
    return Registry.installed({SOURCES, *(c.kind for c in loaded.config.components)})


# -- the API ---------------------------------------------------------------------------


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _finite(value: Any) -> Any:
    """JSON has no NaN or infinity: such a number is null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def make_app(settings: Settings, clock: Clock, backtests: Backtests) -> Starlette:
    """The API, over ``backtests``; the caller starts and stops them.

    What reads files or plug-ins runs on a thread, so the event loop (and
    the health check a supervisor polls) is never held up by a report.
    """

    async def health(request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "time": to_iso(clock.now()),
                "running": backtests.running(),
            }
        )

    def plugins_() -> Response:
        registry = Registry.installed(installed_kinds())
        listed = [
            {
                "kind": kind,
                "name": manifest.name,
                "version": manifest.version,
                "summary": manifest.summary,
            }
            for kind in registry.kinds()
            for manifest in registry.manifests(kind)
        ]
        return JSONResponse({"plugins": listed})

    def plugin_(kind: str, name: str) -> Response:
        if kind not in installed_kinds():
            return _error(404, f"no plug-ins of kind {kind!r}")
        registry = Registry.installed({kind})
        if name not in registry.names(kind):
            return _error(404, f"no {kind} plug-in named {name!r}")
        manifest = registry.get(kind, name).manifest
        return JSONResponse(
            {
                "kind": kind,
                "inputs": inputs_rule(kind),
                "name": manifest.name,
                "version": manifest.version,
                "summary": manifest.summary,
                "params": manifest.params.model_json_schema(),
            }
        )

    def config_text(name: object) -> str | Response:
        """The text of the configuration ``name`` offers, or the error response."""
        if not isinstance(name, str):
            return _error(400, '"config_name" must be a configuration\'s name')
        path = settings.configs / f"{name}.toml" if settings.configs is not None else None
        if path is None or not _ID.match(name) or not path.is_file():
            return _error(404, f"no configuration named {name!r}")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _error(400, f"configuration {name!r} is not UTF-8 text")

    def configs_() -> Response:
        names = (
            sorted(p.stem for p in settings.configs.glob("*.toml") if _ID.match(p.stem))
            if settings.configs is not None
            else []
        )
        return JSONResponse({"configs": [{"name": n} for n in names]})

    def config_(name: str) -> Response:
        text = config_text(name)
        if isinstance(text, Response):
            return text
        return JSONResponse({"name": name, "text": text})

    def submit_(body: object) -> Response:
        if not isinstance(body, dict) or len({"config", "config_name"} & set(body)) != 1:
            return _error(400, 'give either "config" (a configuration\'s text) or "config_name"')
        if "config_name" in body:
            name = body["config_name"]
            text = config_text(name)
            if isinstance(text, Response):
                return text
        else:
            text, name = body["config"], body.get("name", "backtest")
            if not isinstance(text, str):
                return _error(400, '"config" must be a configuration\'s text')
        if not isinstance(name, str) or not _NAME.match(name):
            return _error(400, '"name" must be up to 100 letters, digits, ".", "_" or "-"')
        try:
            loaded = parse_config(text)
        except ConfigError as exc:
            return _error(400, str(exc))
        if loaded.config.mode != "backtest":
            return _error(400, "this configuration is live; only backtests run here yet")
        job = backtests.submit(name, loaded)
        return JSONResponse(
            {"id": job.id, "status": "queued"},
            status_code=202,
            headers={"Location": f"/api/backtests/{job.id}"},
        )

    def listed_() -> Response:
        found = [backtests.describe(i) for i in backtests.ids()]
        return JSONResponse({"backtests": [d for d in found if d is not None]})

    def one_(id_: str) -> Response:
        described = backtests.describe(id_)
        if described is None:
            return _error(404, f"no backtest {id_!r}")
        return JSONResponse(described)

    def chart_(id_: str, read: Callable[[Path], Any]) -> Response:
        """What ``read`` takes from a backtest's journal, as ``{"id", "status", "items"}``:
        a running backtest's too, as far as it has gone."""
        described = backtests.describe(id_)
        if described is None:
            return _error(404, f"no backtest {id_!r}")
        status = described["status"]
        journal = backtests.runs / f"{id_}.sqlite"
        if not journal.is_file() or status.startswith("unreadable"):
            return _error(409, f"backtest {id_} has no journal to chart ({status})")
        try:
            items = read(journal)
        except NotInRunError as exc:
            return _error(404, str(exc))
        except HeliographError as exc:
            return _error(409, f"backtest {id_} cannot be charted: {exc}")
        return JSONResponse({"id": id_, "status": status, "items": items})

    def report_(id_: str) -> Response:
        described = backtests.describe(id_)
        if described is None:
            return _error(404, f"no backtest {id_!r}")
        status = described["status"]
        journal = backtests.runs / f"{id_}.sqlite"
        if status in ("queued", "running", "dropped") or not journal.is_file():
            return _error(409, f"backtest {id_} has no report ({status})")
        if status.startswith("unreadable"):
            return _error(409, f"backtest {id_} has no report: {status}")
        try:
            _, found = figures(journal)
        except HeliographError as exc:
            return _error(409, f"backtest {id_} has no report: {exc}")
        strategies = [{k: _finite(v) for k, v in asdict(f).items()} for f in found]
        return JSONResponse(
            {"id": id_, "status": status, "strategies": strategies, "text": report(journal)}
        )

    async def plugins(request: Request) -> Response:
        return await run_in_threadpool(plugins_)

    def span(request: Request) -> tuple[int | None, int | None] | Response:
        """The ``start`` and ``end`` a chart request names (ISO 8601), or the error."""
        found: list[int | None] = []
        for key in ("start", "end"):
            text = request.query_params.get(key)
            try:
                found.append(None if text is None else from_iso(text))
            except HeliographError as exc:
                return _error(400, f"{key}: {exc}")
        return found[0], found[1]

    async def chart(request: Request, read: Callable[..., Any], *names: str) -> Response:
        """A chart's series: ``read(journal, *path names, start, end)``."""
        between = span(request)
        if isinstance(between, Response):
            return between
        args = [request.path_params[n] for n in names]
        id_ = request.path_params["id"]
        return await run_in_threadpool(chart_, id_, lambda journal: read(journal, *args, *between))

    async def contents_of(request: Request) -> Response:
        return await run_in_threadpool(chart_, request.path_params["id"], charts.contents)

    async def bars_of(request: Request) -> Response:
        stream = request.query_params.get("stream")
        if not stream:
            return _error(400, 'name the stream: ?stream=<mount>@<size>, such as "btc@1h"')
        between = span(request)
        if isinstance(between, Response):
            return between
        return await run_in_threadpool(
            chart_, request.path_params["id"], lambda j: charts.bars(j, stream, *between)
        )

    async def node_of(request: Request) -> Response:
        return await chart(request, charts.node, "name")

    async def signals_of(request: Request) -> Response:
        strategy = request.query_params.get("strategy")
        between = span(request)
        if isinstance(between, Response):
            return between
        return await run_in_threadpool(
            chart_, request.path_params["id"], lambda j: charts.signals(j, strategy, *between)
        )

    async def fills_of(request: Request) -> Response:
        strategy = request.query_params.get("strategy")
        between = span(request)
        if isinstance(between, Response):
            return between
        return await run_in_threadpool(
            chart_, request.path_params["id"], lambda j: charts.fills(j, strategy, *between)
        )

    async def equity_of(request: Request) -> Response:
        return await chart(request, charts.equity_of, "strategy")

    async def plugin(request: Request) -> Response:
        params = request.path_params
        return await run_in_threadpool(plugin_, params["kind"], params["name"])

    async def configs(request: Request) -> Response:
        return await run_in_threadpool(configs_)

    async def config(request: Request) -> Response:
        return await run_in_threadpool(config_, request.path_params["name"])

    async def submit(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "the body is not JSON")
        return await run_in_threadpool(submit_, body)

    async def listed(request: Request) -> Response:
        return await run_in_threadpool(listed_)

    async def one(request: Request) -> Response:
        return await run_in_threadpool(one_, request.path_params["id"])

    async def report_of(request: Request) -> Response:
        return await run_in_threadpool(report_, request.path_params["id"])

    routes = [
        Route("/api/health", health),
        Route("/api/plugins", plugins),
        Route("/api/plugins/{kind}/{name}", plugin),
        Route("/api/configs", configs),
        Route("/api/configs/{name}", config),
        Route("/api/backtests", submit, methods=["POST"]),
        Route("/api/backtests", listed),
        Route("/api/backtests/{id}", one),
        Route("/api/backtests/{id}/report", report_of),
        Route("/api/backtests/{id}/contents", contents_of),
        Route("/api/backtests/{id}/bars", bars_of),
        Route("/api/backtests/{id}/nodes/{name}", node_of),
        Route("/api/backtests/{id}/signals", signals_of),
        Route("/api/backtests/{id}/fills", fills_of),
        Route("/api/backtests/{id}/equity/{strategy}", equity_of),
    ]

    async def not_found(request: Request, exc: Exception) -> Response:
        return _error(404, f"nothing at {request.url.path}")

    async def not_allowed(request: Request, exc: Exception) -> Response:
        return _error(405, f"{request.method} is not allowed at {request.url.path}")

    async def crashed(request: Request, exc: Exception) -> Response:
        _log.error(
            "request crashed",
            exc_info=exc,
            extra={"fields": {"method": request.method, "path": request.url.path}},
        )
        return _error(500, f"a bug: {type(exc).__name__}: {exc}")

    return Starlette(
        routes=routes,
        exception_handlers={404: not_found, 405: not_allowed, Exception: crashed},
    )


# -- the process -----------------------------------------------------------------------


def settings_from(
    data: str | None,
    configs: str | None,
    host: str | None,
    port: int | None,
    environ: Mapping[str, str] = os.environ,
) -> Settings:
    """The settings from options, then the environment, then the defaults.

    Raises:
        ConfigError: if there is no data folder, or the port is not a port number.
    """
    data = data or environ.get("HELIOGRAPH_DATA")
    if not data:
        raise ConfigError("give a data folder: --data, or HELIOGRAPH_DATA")
    configs = configs or environ.get("HELIOGRAPH_CONFIGS") or None
    if port is None:
        text = environ.get("HELIOGRAPH_PORT", str(DEFAULT_PORT))
        if not text.isdigit():
            raise ConfigError(f"HELIOGRAPH_PORT must be a port number, not {text!r}")
        port = int(text)
    if not 0 <= port <= 65535:
        raise ConfigError(f"the port must be from 0 to 65535, not {port}")
    return Settings(
        data=Path(data),
        configs=Path(configs) if configs else None,
        host=host or environ.get("HELIOGRAPH_HOST", "127.0.0.1"),
        port=port,
    )


class _Server(uvicorn.Server):
    """uvicorn's server, telling the backtests to stop the moment a signal comes,
    not only once its open requests have finished."""

    def __init__(self, config: uvicorn.Config, stopping: "_Stopping") -> None:
        super().__init__(config)
        self._stopping = stopping

    def handle_exit(self, sig: int, frame: Any) -> None:
        self._stopping.handle(sig, frame)
        super().handle_exit(sig, frame)


@dataclass(slots=True)
class _Stopping:
    """When the process was told to stop, and what that sets going.

    ``handle`` runs in a signal handler, so it only sets plain attributes: no
    lock, no logging, which a second signal arriving inside the first could
    deadlock on. The rest is done by ``serve`` once the server has stopped.
    """

    backtests: Backtests | None = None
    server: _Server | None = None
    since: float | None = None  # time.monotonic() at the first signal

    def handle(self, number: int, frame: object) -> None:
        # Before uvicorn installs its own handlers, and again when it raises the
        # signal that stopped it once it has shut down: the process stops, and
        # exits 0 rather than dying of the signal.
        if self.since is None:
            self.since = time.monotonic()
        if self.backtests is not None:
            self.backtests.signal_stop()
        if self.server is not None:
            self.server.should_exit = True

    def left(self) -> float:
        """Seconds left until the process must have exited."""
        since = time.monotonic() if self.since is None else self.since
        return STOP_DEADLINE - (time.monotonic() - since)


def serve(settings: Settings) -> int:
    """Run the engine process until it is told to stop (SIGTERM or SIGINT); its exit
    status: 0, or 1 if a backtest had not stopped by the deadline.

    Raises:
        ConfigError: if a folder is not one, or the address cannot be listened on.
    """
    stopping = _Stopping()
    signal.signal(signal.SIGTERM, stopping.handle)
    signal.signal(signal.SIGINT, stopping.handle)
    clock = WallClock()
    _logging(clock)
    if settings.configs is not None and not settings.configs.is_dir():
        raise ConfigError(f"configurations folder {settings.configs} is not a folder")
    if settings.data.exists() and not settings.data.is_dir():
        raise ConfigError(f"data folder {settings.data} is not a folder")
    backtests = Backtests(settings.data / "runs", clock)
    listener = _listen(settings.host, settings.port)
    try:
        backtests.start()
        stopping.backtests = backtests
        config = uvicorn.Config(
            make_app(settings, clock, backtests),
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=CLOSING,
            workers=1,
        )
        server = stopping.server = _Server(config, stopping)
        host, port = listener.getsockname()[:2]
        _event("listening", host=host, port=port, data=str(settings.data), pid=os.getpid())
        if stopping.since is None:  # a signal already came: no need to start
            server.run(sockets=[listener])
    finally:
        stopping.handle(0, None)
        _event("stopping")
        backtests.stop()
        listener.close()
    status = 0
    if not backtests.wait(stopping.left()):
        _event(
            "a backtest did not finish stopping in time",
            logging.ERROR,
            id=backtests.running(),
            deadline=STOP_DEADLINE,
        )
        status = 1
    elif not _threads_done(stopping.left()):
        # Requests still being answered (a long report) on threads Python would
        # wait for at exit.
        _event("requests did not finish in time", logging.ERROR, deadline=STOP_DEADLINE)
        status = 1
    else:
        _event("stopped")
    # A signal from here on, while Python winds down, would otherwise end it.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    return status


def _threads_done(timeout: float) -> bool:
    """Whether every other thread Python waits for at exit has ended, waiting up to
    ``timeout`` seconds for them all."""
    deadline = time.monotonic() + timeout
    for thread in threading.enumerate():
        if thread is threading.current_thread() or thread.daemon:
            continue
        thread.join(max(deadline - time.monotonic(), 0.0))
        if thread.is_alive():
            return False
    return True


def _listen(host: str, port: int) -> socket.socket:
    """A socket listening at ``host:port``, so its port is known before serving."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        family = infos[0][0]
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(128)
    except (OSError, OverflowError) as exc:
        raise ConfigError(
            f"cannot listen on {host}:{port}: {getattr(exc, 'strerror', None) or exc}"
        ) from exc
    return listener
