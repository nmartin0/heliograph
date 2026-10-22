# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Fill models: what a simulated fill costs (core/broker.py, decision D31)."""

import math
from typing import Self

from pydantic import model_validator

from heliograph.plugins.manifest import Manifest, Params, Plugin, param


class NextOpenParams(Params):
    spread: float = param(
        0.0, description="Bid-ask spread, as a fraction of the price; half is paid each way", ge=0.0
    )
    slippage: float = param(
        0.0, description="Slippage beyond the spread, as a fraction of the price", ge=0.0
    )
    commission: float = param(
        0.0, description="Commission, as a fraction of the value traded", ge=0.0, le=1.0
    )

    @model_validator(mode="after")
    def _a_sale_fetches_something(self) -> Self:
        if self.spread / 2 + self.slippage >= 1:
            raise ValueError(
                f"half the spread plus the slippage ({self.spread / 2 + self.slippage}) "
                "must be below 1, or a sale would fetch nothing"
            )
        return self


class NextOpen(Plugin[NextOpenParams]):
    """Fills at the next bar's open, worse by half the spread and the slippage.

    Buying pays ``open * (1 + spread / 2 + slippage)``; selling receives
    ``open * (1 - spread / 2 - slippage)``. The commission is
    ``commission * |quantity| * price``.
    """

    manifest = Manifest(
        kind="fills",
        name="next_open",
        version="1",
        summary="Fill at the next open, paying half the spread, slippage and a commission",
        params=NextOpenParams,
    )

    def price(self, open_: float, quantity: float) -> float:
        worse = self.params.spread / 2 + self.params.slippage
        return open_ * (1 + math.copysign(worse, quantity))

    def commission(self, price: float, quantity: float) -> float:
        return self.params.commission * abs(quantity) * price
