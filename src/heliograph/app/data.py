# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Downloading a backtest's history into the store, and publishing it.

The usual way to run a backtest on downloaded data:

1. ``download_mounts`` asks each mount's source for its history and keeps
   what comes back as raw files (the mounts are built and checked first, so
   nothing is fetched for a configuration that cannot run);
2. ``publish_mounts`` publishes the mounts' datasets as one snapshot;
3. the backtest mounts the ``store`` source on that snapshot, and records
   its id.

Re-running step 1 later adds raw files; publishing again then gives a new
snapshot, and the old one still rebuilds from the files it was made from.
"""

from dataclasses import dataclass

from heliograph.app.config import ConfigError, RunConfig
from heliograph.app.limits import Sleep
from heliograph.app.mounts import build_mounts
from heliograph.core.clock import Clock
from heliograph.core.sources import DataSource
from heliograph.plugins.registry import Registry
from heliograph.plugins.store import Dataset, Download, Snapshot, Store


@dataclass(frozen=True, slots=True)
class Downloaded:
    """A mount's download."""

    mount: str
    download: Download


async def download_mounts(
    config: RunConfig, registry: Registry, store: Store, clock: Clock, sleep: Sleep
) -> list[Downloaded]:
    """Download every mount's history into ``store``, one mount after another.

    ``clock`` and ``sleep`` are the real time, as for ``build_mounts``. Every
    source is closed afterwards, whether or not the downloads succeeded.

    Raises:
        ConfigError: if the configuration is live, or a mount cannot be served.
        Whatever a source raised; the downloads before it are kept.
    """
    if config.mode != "backtest":
        raise ConfigError("history is downloaded for a backtest; this configuration is live")
    mounts = build_mounts(config, registry, clock, sleep)
    done: list[Downloaded] = []
    try:
        for mount in mounts:
            spec = config.mounts[mount.name]
            assert mount.request is not None  # a backtest mount asks for history
            raw = await store.download(spec.source, spec.params, mount.source, mount.request, clock)
            done.append(Downloaded(mount.name, raw))
    finally:
        sources: dict[int, DataSource] = {id(m.source): m.source for m in mounts}
        for source in sources.values():
            await source.close()
    return done


def mount_datasets(config: RunConfig) -> list[Dataset]:
    """The dataset each mount of ``config`` reads, in mount order, each once."""
    found: dict[str, Dataset] = {}
    for spec in config.mounts.values():
        dataset = Dataset(
            source=spec.source,
            instrument=spec.instrument.instrument(),
            kind=spec.kind,
            bar_size=spec.bar_size_ns,
        )
        found.setdefault(dataset.name, dataset)
    return list(found.values())


def publish_mounts(config: RunConfig, store: Store) -> Snapshot:
    """Publish the datasets of ``config``'s mounts, from all their raw files, as one snapshot.

    Raises:
        StoreError: if a dataset has nothing downloaded or fails its audit.
    """
    return store.publish(mount_datasets(config))
