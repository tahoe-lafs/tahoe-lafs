import os
from typing import Optional


def foolscap_only_for_integration_testing() -> Optional[bool]:
    """
    Return whether HTTP storage protocol has been disabled / Foolscap
    forced, for purposes of integration testing.

    This is determined by the __TAHOE_INTEGRATION_FORCE_FOOLSCAP environment
    variable, which can be 1, 0, or not set, corresponding to results of
    ``True``, ``False`` and ``None`` (i.e. default).
    """
    force_foolscap = os.environ.get("__TAHOE_INTEGRATION_FORCE_FOOLSCAP")
    if force_foolscap is None:
        return None

    return bool(int(force_foolscap))
