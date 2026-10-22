# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Publish every dataset under a store root; print the snapshot id.

Run as ``python -m tests.determinism.publish <root>`` in a fresh interpreter,
so the hash seed and time zone are the process's own.
"""

import sys
from pathlib import Path

from heliograph.plugins.store import Store


def main(root: str) -> None:
    store = Store(Path(root))
    print(store.publish(store.datasets()).id)


if __name__ == "__main__":
    main(sys.argv[1])
