# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Settings shared by the whole suite."""

from hypothesis import settings

# Hypothesis fails an example that takes longer than its deadline (200 ms by
# default). That measures the machine, not the code: on a slower or busier
# computer a correct test fails at random. Correctness is what the suite checks;
# a test that hangs is caught by the control runner's timeout instead.
settings.register_profile("heliograph", deadline=None)
settings.load_profile("heliograph")
