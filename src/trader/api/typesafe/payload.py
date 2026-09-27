"""Unwrap futu (code, data) payload tuples into concrete Python types."""

from __future__ import annotations

import pandas as pd
from futu import RET_ERROR


def _unwrap_payload[T](payload: tuple[int, object], expected: type[T]) -> T:
    """Unwrap a futu payload when the SDK reported success.

    Args:
        payload: Raw (code, data) tuple returned by the futu SDK.
        expected: Concrete type the payload data must be an instance of.

    Returns:
        The payload data, narrowed to ``expected``.

    Raises:
        RuntimeError: When the SDK reported an error or the payload type mismatches.
    """
    code, data = payload
    if code == RET_ERROR:
        raise RuntimeError(str(payload))
    if isinstance(data, expected):
        return data
    raise RuntimeError(f"unexpected payload type: {type(data)}")


class Payload:
    @staticmethod
    def df_payload(payload: tuple[int, str | pd.DataFrame]) -> pd.DataFrame:
        """Convert payload to DataFrame.

        Args:
            payload: Payload as string or DataFrame.

        Returns:
            pd.DataFrame: Converted DataFrame.
        """
        return _unwrap_payload(payload, pd.DataFrame)

    @staticmethod
    def str_payload(payload: tuple[int, str]) -> str:
        """Convert payload to string.

        Args:
            payload: Payload as string or DataFrame.

        Returns:
            str: Converted string.
        """
        return _unwrap_payload(payload, str)
