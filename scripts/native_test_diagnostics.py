"""Pytest phase boundaries for the trusted, disposable native verification child."""

import pytest

from scripts.native_test_process import record_phase


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection(session):
    record_phase("collection")
    yield
    record_phase("collection", event="finished")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    record_phase("setup", nodeid=item.nodeid)
    yield
    record_phase("setup", event="finished", nodeid=item.nodeid)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    record_phase("call", nodeid=item.nodeid)
    yield
    record_phase("call", event="finished", nodeid=item.nodeid)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    record_phase("teardown", nodeid=item.nodeid)
    yield
    record_phase("teardown", event="finished", nodeid=item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    record_phase("session", event="finished")
