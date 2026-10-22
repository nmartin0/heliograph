# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""From a file to a backtest on a published snapshot: download, publish, mount, read."""

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.app.config import ConfigError, parse_config
from heliograph.app.data import download_mounts, mount_datasets, publish_mounts
from heliograph.app.mounts import build_mounts
from heliograph.core.messages import Point
from heliograph.core.sources import SourceUnavailableError
from heliograph.core.time import NANOS_PER_MINUTE, from_iso
from heliograph.plugins.file_source import FileSource
from heliograph.plugins.registry import Registry
from heliograph.plugins.store import Store, StoreSource
from tests.support.simulated import SimulatedTime

MIN = NANOS_PER_MINUTE
NOW = "2026-10-21T00:00:00Z"
INSTRUMENT = """
[mounts.{name}.instrument]
symbol = "{symbol}"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""


class Closing(FileSource):
    """The file source, counting how often it is closed."""

    manifest = FileSource.manifest
    closed: ClassVar[list[str]] = []

    async def close(self) -> None:
        Closing.closed.append(self.params.path)


@pytest.fixture
def registry() -> Registry:
    Closing.closed.clear()
    registry = Registry()
    registry.register(Closing)
    registry.register(StoreSource)
    return registry


def csv(tmp_path: Path, name: str, *rows: str) -> Path:
    path = tmp_path / name
    path.write_text("time,price,size\n" + "".join(f"{row}\n" for row in rows))
    return path


def backtest(**files: tuple[Path, str]) -> str:
    """A backtest with one file mount per name: (file, symbol)."""
    text = ""
    for name, (path, symbol) in files.items():
        text += (
            f"[mounts.{name}]\n"
            'source = "file"\n'
            f'params = {{ path = {str(path)!r}, live_delay = "1m" }}\n'
            'kind = "trades"\n'
            "start = 2026-10-01T00:00:00Z\n"
            "end = 2026-10-02T00:00:00Z\n"
        ) + INSTRUMENT.format(name=name, symbol=symbol)
    return text


def time() -> SimulatedTime:
    return SimulatedTime(start=from_iso(NOW))


def download(text: str, registry: Registry, store: Store) -> list[Any]:
    t = time()
    config = parse_config(text).config
    return asyncio.run(download_mounts(config, registry, store, t.clock, t.advance))


def test_a_backtest_downloaded_and_published_is_read_back_through_a_store_mount(
    tmp_path: Path, registry: Registry
) -> None:
    btc = csv(tmp_path, "btc.csv", "2026-10-01T00:00:00Z,100,1", "2026-10-01T00:01:00Z,0,1")
    eth = csv(tmp_path, "eth.csv", "2026-10-01T00:02:00Z,5,2")
    text = backtest(btc=(btc, "BTC/USD"), eth=(eth, "ETH/USD"))
    store = Store(tmp_path / "store")

    done = download(text, registry, store)
    assert [(d.mount, d.download.rows) for d in done] == [("btc", 2), ("eth", 1)]
    assert sorted(Closing.closed) == sorted([str(btc), str(eth)])

    snapshot = publish_mounts(parse_config(text).config, store)
    assert store.verify(snapshot.id) == []

    reading = text.replace('source = "file"', 'source = "store"')
    for path in (btc, eth):
        reading = reading.replace(
            f'params = {{ path = {str(path)!r}, live_delay = "1m" }}',
            f"params = {{ root = {str(store.root)!r}, "
            f'snapshot = "{snapshot.id}", source = "file" }}',
        )
    t = time()
    (btc_mount, _) = build_mounts(parse_config(reading).config, registry, t.clock, t.advance)
    assert btc_mount.request is not None
    with SourceLoop() as loop:
        got = list(HistoryFeed(loop, btc_mount.source, btc_mount.request))
    start = from_iso("2026-10-01T00:00:00Z")
    # The zero price was quarantined; the delay of the file the data came from still applies.
    assert [(m.ts - start, m.values["price"]) for m in got if isinstance(m, Point)] == [
        (MIN, 100.0)
    ]


def test_mounts_on_one_dataset_are_published_once(tmp_path: Path) -> None:
    btc = tmp_path / "btc.csv"
    text = backtest(a=(btc, "BTC/USD"), b=(btc, "BTC/USD"))
    assert [d.name for d in mount_datasets(parse_config(text).config)] == [
        "file/kraken%3ABTC%2FUSD/trades"
    ]


def test_history_is_downloaded_for_a_backtest_only(tmp_path: Path, registry: Registry) -> None:
    live = 'mode = "live"\n' + backtest(btc=(tmp_path / "btc.csv", "BTC/USD"))
    live = live.replace("start = 2026-10-01T00:00:00Z\nend = 2026-10-02T00:00:00Z\n", "")
    with pytest.raises(ConfigError, match="this configuration is live"):
        download(live, registry, Store(tmp_path))


def test_a_failed_download_keeps_those_before_it_and_closes_every_source(
    tmp_path: Path, registry: Registry
) -> None:
    btc = csv(tmp_path, "btc.csv", "2026-10-01T00:00:00Z,100,1")
    text = backtest(btc=(btc, "BTC/USD"), eth=(tmp_path / "missing.csv", "ETH/USD"))
    store = Store(tmp_path / "store")
    with pytest.raises(SourceUnavailableError, match=r"missing\.csv"):
        download(text, registry, store)
    assert len(store.datasets()) == 1
    assert len(Closing.closed) == 2
