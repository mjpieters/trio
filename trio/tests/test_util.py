import pytest

import signal
import sys
import textwrap

from async_generator import async_generator, yield_

from .._util import *
from .. import _core
from ..testing import wait_all_tasks_blocked, assert_checkpoints


def test_signal_raise():
    record = []

    def handler(signum, _):
        record.append(signum)

    old = signal.signal(signal.SIGFPE, handler)
    try:
        signal_raise(signal.SIGFPE)
    finally:
        signal.signal(signal.SIGFPE, old)
    assert record == [signal.SIGFPE]


async def test_ConflictDetector():
    ul1 = ConflictDetector("ul1")
    ul2 = ConflictDetector("ul2")

    async with ul1:
        with assert_checkpoints():
            async with ul2:
                print("ok")

    with pytest.raises(_core.ResourceBusyError) as excinfo:
        async with ul1:
            with assert_checkpoints():
                async with ul1:
                    pass  # pragma: no cover
    assert "ul1" in str(excinfo.value)

    async def wait_with_ul1():
        async with ul1:
            await wait_all_tasks_blocked()

    with pytest.raises(_core.ResourceBusyError) as excinfo:
        async with _core.open_nursery() as nursery:
            nursery.start_soon(wait_with_ul1)
            nursery.start_soon(wait_with_ul1)
    assert "ul1" in str(excinfo.value)

    # mixing sync and async entry
    with pytest.raises(_core.ResourceBusyError) as excinfo:
        with ul1.sync:
            with assert_checkpoints():
                async with ul1:
                    pass  # pragma: no cover
    assert "ul1" in str(excinfo.value)


async def test_contextmanager_do_not_unchain_non_stopiteration_exceptions():
    @acontextmanager
    @async_generator
    async def manager_issue29692():
        try:
            await yield_()
        except Exception as exc:
            raise RuntimeError('issue29692:Chained') from exc

    with pytest.raises(RuntimeError) as excinfo:
        async with manager_issue29692():
            raise ZeroDivisionError
    assert excinfo.value.args[0] == 'issue29692:Chained'
    assert isinstance(excinfo.value.__cause__, ZeroDivisionError)

    # This is a little funky because of implementation details in
    # async_generator It can all go away once we stop supporting Python3.5
    with pytest.raises(RuntimeError) as excinfo:
        async with manager_issue29692():
            exc = StopIteration('issue29692:Unchained')
            raise exc
    assert excinfo.value.args[0] == 'issue29692:Chained'
    cause = excinfo.value.__cause__
    assert cause.args[0] == 'generator raised StopIteration'
    assert cause.__cause__ is exc

    with pytest.raises(StopAsyncIteration) as excinfo:
        async with manager_issue29692():
            raise StopAsyncIteration('issue29692:Unchained')
    assert excinfo.value.args[0] == 'issue29692:Unchained'
    assert excinfo.value.__cause__ is None

    @acontextmanager
    @async_generator
    async def noop_async_context_manager():
        await yield_()

    with pytest.raises(StopIteration):
        async with noop_async_context_manager():
            raise StopIteration


# Native async generators are only available from Python 3.6 and onwards
nativeasyncgenerators = True
try:
    exec(
        """
@acontextmanager
async def manager_issue29692_2():
    try:
        yield
    except Exception as exc:
        raise RuntimeError('issue29692:Chained') from exc
"""
    )
except SyntaxError:
    nativeasyncgenerators = False


@pytest.mark.skipif(
    not nativeasyncgenerators,
    reason="Python < 3.6 doesn't have native async generators"
)
async def test_native_contextmanager_do_not_unchain_non_stopiteration_exceptions(
):

    with pytest.raises(RuntimeError) as excinfo:
        async with manager_issue29692_2():
            raise ZeroDivisionError
    assert excinfo.value.args[0] == 'issue29692:Chained'
    assert isinstance(excinfo.value.__cause__, ZeroDivisionError)

    for cls in [StopIteration, StopAsyncIteration]:
        with pytest.raises(cls) as excinfo:
            async with manager_issue29692_2():
                raise cls('issue29692:Unchained')
        assert excinfo.value.args[0] == 'issue29692:Unchained'
        assert excinfo.value.__cause__ is None


async def test_acontextmanager_exception_passthrough():
    # This was the cause of annoying coverage flapping, see gh-140
    @acontextmanager
    @async_generator
    async def noop_async_context_manager():
        await yield_()

    for exc_type in [StopAsyncIteration, RuntimeError, ValueError]:
        with pytest.raises(exc_type):
            async with noop_async_context_manager():
                raise exc_type


async def test_acontextmanager_catches_exception():
    @acontextmanager
    @async_generator
    async def catch_it():
        with pytest.raises(ValueError):
            await yield_()

    async with catch_it():
        raise ValueError


async def test_acontextmanager_no_yield():
    @acontextmanager
    @async_generator
    async def yeehaw():
        pass

    with pytest.raises(RuntimeError) as excinfo:
        async with yeehaw():
            assert False  # pragma: no cover

    assert "didn't yield" in str(excinfo.value)


async def test_acontextmanager_too_many_yields():
    @acontextmanager
    @async_generator
    async def doubleyield():
        try:
            await yield_()
        except Exception:
            pass
        await yield_()

    with pytest.raises(RuntimeError) as excinfo:
        async with doubleyield():
            pass

    assert "didn't stop" in str(excinfo.value)

    with pytest.raises(RuntimeError) as excinfo:
        async with doubleyield():
            raise ValueError

    assert "didn't stop after athrow" in str(excinfo.value)


async def test_acontextmanager_requires_asyncgenfunction():
    with pytest.raises(TypeError):

        @acontextmanager
        def syncgen():  # pragma: no cover
            yield


def test_module_metadata_is_fixed_up():
    import trio
    assert trio.Cancelled.__module__ == "trio"
    assert trio.open_cancel_scope.__module__ == "trio"
    assert trio.ssl.SSLStream.__module__ == "trio.ssl"
    assert trio.abc.Stream.__module__ == "trio.abc"
    assert trio.hazmat.wait_task_rescheduled.__module__ == "trio.hazmat"
    import trio.testing
    assert trio.testing.trio_test.__module__ == "trio.testing"

    # Also check methods
    assert trio.ssl.SSLStream.__init__.__module__ == "trio.ssl"
    assert trio.abc.Stream.send_all.__module__ == "trio.abc"
