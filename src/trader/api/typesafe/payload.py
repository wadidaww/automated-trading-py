import pandas as pd
from futu import RET_ERROR


class Payload:
    @staticmethod
    def df_payload(payload: tuple[int, str | pd.DataFrame]) -> pd.DataFrame:
        """Convert payload to DataFrame.

        Args:
            payload: Payload as string or DataFrame.

        Returns:
            pd.DataFrame: Converted DataFrame.
        """
        code, data = payload
        if code == RET_ERROR:
            raise RuntimeError(str(payload))
        if isinstance(data, pd.DataFrame):
            return data
        raise RuntimeError(f"unexpected payload type: {type(data)}")

    @staticmethod
    def str_payload(payload: tuple[int, str]) -> str:
        """Convert payload to string.

        Args:
            payload: Payload as string or DataFrame.

        Returns:
            str: Converted string.
        """
        code, data = payload
        if code == RET_ERROR:
            raise RuntimeError(str(payload))
        if isinstance(data, str):
            return data
        raise RuntimeError(f"unexpected payload type: {type(data)}")
