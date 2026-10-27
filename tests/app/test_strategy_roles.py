# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The strategy's roles, said once, agree with the strategy they describe."""

from typing import Any, get_args, get_origin

from heliograph.app.config import STRATEGY_ROLES, StrategyConfig


def test_every_role_is_a_field_of_a_strategy_and_every_component_field_is_a_role() -> None:
    fields = StrategyConfig.model_fields
    roles = [role for role, _, _ in STRATEGY_ROLES]
    assert set(roles) <= set(fields)
    assert set(fields) - set(roles) == {"name", "price"}  # the two that are not components


def test_a_role_takes_many_exactly_when_its_field_is_a_tuple() -> None:
    fields = StrategyConfig.model_fields
    for role, _, many in STRATEGY_ROLES:
        annotation: Any = fields[role].annotation
        assert (get_origin(annotation) is tuple) is many, role
        if not many:
            assert str in get_args(annotation) or annotation is str, role


def test_the_kind_of_a_role_is_the_kind_the_strategy_says_it_must_be() -> None:
    strategy = StrategyConfig(name="s", price="b@1h.close", forecasters=("f",), sizer="z")
    assert [kind for _, kind, _ in STRATEGY_ROLES] == list(strategy.stages())
