import pytest
from pydantic import ValidationError

from table_review.settings import Settings


@pytest.mark.parametrize(
    "origin",
    [
        "http://reviews.example.com",
        "https://reviews.example.com/path",
        "https://user:secret@reviews.example.com",
        "https://reviews.example.com?query=1",
        "https://reviews.example.com#fragment",
        "https://reviews.example.com/",
        "https://reviews.example.com:invalid",
        "https://",
    ],
)
def test_hosted_mode_rejects_invalid_or_non_https_origins(origin):
    with pytest.raises(ValidationError, match="HTTPS origin"):
        Settings(deployment_mode="hosted", public_origin=origin)


def test_hosted_mode_cannot_disable_secure_cookies():
    with pytest.raises(ValidationError, match="secure cookies"):
        Settings(
            deployment_mode="hosted",
            public_origin="https://reviews.example.com",
            secure_cookies=False,
        )


def test_hosted_env_requires_the_same_guards(monkeypatch):
    monkeypatch.setenv("TABLE_REVIEW_DATABASE_URL", "fixture")
    monkeypatch.setenv("TABLE_REVIEW_DEPLOYMENT_MODE", "hosted")
    monkeypatch.setenv("TABLE_REVIEW_PUBLIC_ORIGIN", "http://localhost:8000")
    with pytest.raises(ValidationError, match="HTTPS origin"):
        Settings.from_env()


def test_hosted_https_origin_and_local_http_remain_supported():
    assert Settings(deployment_mode="hosted", public_origin="https://localhost:8443").secure_cookies
    assert not Settings(public_origin="http://127.0.0.1:8000", secure_cookies=False).secure_cookies
