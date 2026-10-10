"""Run the trading pipeline: live opt-in gate, client setup, signals and shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal

from trader.api.broker import BrokerClient
from trader.api.factory import create_client
from trader.api.quote_handler import QuoteHandler
from trader.api.quote_poller import QuotePoller
from trader.pipeline.pipeline import TradingPipeline
from trader.risk.kill_switch import KillSwitch
from trader.utils.config import AppConfig, load_config
from trader.utils.logger import get_logger

logger = get_logger("main")

LIVE_CONFIRM_ENV = "TRADER_LIVE_CONFIRM"


class LiveModeRefusedError(RuntimeError):
    """Live trading was requested without every required opt-in."""


def check_live_opt_in(config: AppConfig, mode: str) -> None:
    """Refuse live trading unless every opt-in agrees.

    Live requires ``--mode live``, ``trading.trd_env: REAL``, ``TRADER_LIVE_CONFIRM=1`` and a
    numeric ``trading.account_id``. A REAL config without ``--mode live`` is refused too, so the
    two can never silently disagree.

    Raises:
        LiveModeRefusedError: When any requirement is missing.
    """
    wants_real = config.trading.trd_env == "REAL"
    if mode != "live":
        if wants_real:
            raise LiveModeRefusedError("config trd_env is REAL but --mode is not live")
        return
    if not wants_real:
        raise LiveModeRefusedError("--mode live requires trading.trd_env: REAL in the config")
    if os.environ.get(LIVE_CONFIRM_ENV) != "1":
        raise LiveModeRefusedError(f"--mode live requires {LIVE_CONFIRM_ENV}=1")
    if not config.trading.account_id.isdigit():
        raise LiveModeRefusedError("--mode live requires a numeric trading.account_id")


def build_client(config: AppConfig, mode: str) -> BrokerClient:
    """Build the broker client for ``config.trading.market`` (see ``trader.api.factory``).

    Live mode never falls back to the paper simulator: an unreachable gateway is an error.

    Args:
        config: Application configuration.
        mode: Trading mode (paper or live).

    Returns:
        Configured client: ``FutuClient`` for equities, ``FutuCryptoClient`` for crypto.
    """
    return create_client(config, mode)


async def prepare_live(client: BrokerClient, config: AppConfig) -> None:
    """Verify the REAL account and unlock trading before the first decision.

    Raises:
        LiveModeRefusedError: When the unlock secret is missing.
        RuntimeError: When the account is not listed or OpenD refuses the unlock.
    """
    await client.verify_account()
    password_md5 = os.environ.get(config.opend.unlock_password_md5_env, "")
    if not password_md5:
        raise LiveModeRefusedError(f"{config.opend.unlock_password_md5_env} is not set")
    await client.unlock_trade(password_md5)


async def _run_pipeline(client: BrokerClient, config: AppConfig, duration: int) -> None:
    """Start the pipeline and quote poller, then run until the duration ends or SIGTERM.

    SIGTERM and SIGINT stop the run gracefully; SIGUSR1 trips the kill switch.

    Args:
        client: Connected broker client.
        config: Application configuration.
        duration: Runtime duration in seconds.
    """
    pipeline = TradingPipeline(config=config, client=client)
    stop = asyncio.Event()
    kill_switch = pipeline.risk_stage.kill_switch
    loop = asyncio.get_running_loop()
    handlers = {
        signal.SIGTERM: stop.set,
        signal.SIGINT: stop.set,
        signal.SIGUSR1: lambda: kill_switch.trip("signal:SIGUSR1"),
    }
    for signum, handler in handlers.items():
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signum, handler)
    poller = QuotePoller(
        client=client,
        handler=QuoteHandler(pipeline.input_queue),
        symbols=config.trading.symbols,
        interval_s=float(config.trading.signal_cooldown_s),
    )

    await pipeline.start()
    await poller.start()

    logger.info(
        "pipeline_running",
        duration_s=duration,
        symbols=config.trading.symbols,
        interval_s=config.trading.signal_cooldown_s,
    )

    watcher = asyncio.create_task(_watch_kill_switch(kill_switch))
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=duration)
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher
    try:
        await _shutdown(poller, pipeline, client, cancel_on_exit=config.risk.cancel_on_exit)
    finally:
        for signum in handlers:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(signum)


async def _watch_kill_switch(kill_switch: KillSwitch, interval_s: float = 1.0) -> None:
    """Poll the kill-switch trigger file so a halt does not wait for the next signal."""
    while not kill_switch.is_tripped():
        await asyncio.sleep(interval_s)


async def _shutdown(
    poller: QuotePoller,
    pipeline: TradingPipeline,
    client: BrokerClient,
    *,
    cancel_on_exit: bool,
) -> None:
    """Stop new orders, drain, apply the cancel-on-exit policy, then stop the pipeline.

    Args:
        poller: Running quote poller.
        pipeline: Running trading pipeline.
        client: Client whose working orders are cancelled when ``cancel_on_exit``.
        cancel_on_exit: Whether to cancel every working order before exiting.
    """
    await poller.stop()
    await pipeline.drain()
    await pipeline.stop()
    if cancel_on_exit:
        try:
            await client.cancel_all_orders()
            logger.info("cancel_on_exit_done")
        except Exception:
            logger.exception("cancel_on_exit_failed")


async def run(mode: str, duration: int, config_path: str) -> None:
    """Run the trading pipeline.

    Args:
        mode: Trading mode (paper or live).
        duration: Runtime duration in seconds.
        config_path: Path to configuration file.
    """
    config = load_config(config_path)
    logger.info("config_loaded", config_path=config_path, mode=mode)
    check_live_opt_in(config, mode)

    async with build_client(config, mode) as client:
        if mode == "live":
            await prepare_live(client, config)
        logger.info(
            "opend_connected",
            host=config.opend.host,
            port=config.opend.port,
            mode=mode,
            trd_env=client.trd_env,
        )
        await _run_pipeline(client, config, duration)

    logger.info("pipeline_shutdown_complete")
