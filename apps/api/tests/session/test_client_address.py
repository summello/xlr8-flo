import pytest
from starlette.requests import Request

from flo.kernel.config import Settings
from flo.kernel.session.client_address import throttle_client_value, trusted_client_host
from flo.kernel.session.store import request_device

SECRET = "test-only-origin-credential-123456789"


def request(headers, host="198.51.100.8"):
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (host, 1234) if host else None,
        }
    )


@pytest.mark.parametrize(
    "secret,address,expected",
    [
        (None, "203.0.113.2", None),
        ("wrong", "203.0.113.2", None),
        (SECRET, "203.0.113.2", "203.0.113.2"),
        (SECRET, "invalid", None),
        (SECRET, None, None),
    ],
)
def test_trusted_address_boundary(monkeypatch, secret, address, expected):
    monkeypatch.setattr(
        "flo.kernel.session.client_address._settings", lambda: Settings(origin_shared_secret=SECRET)
    )
    headers = {}
    if secret:
        headers["X-FLO-Origin-Secret"] = secret
    if address:
        headers["X-FLO-Client-IP"] = address
    r = request(headers)
    assert trusted_client_host(r) == expected
    assert throttle_client_value(r) == (expected or "198.51.100.8")
    assert request_device(r).ip_prefix == ("203.0.113.0/24" if expected else "198.51.100.0/24")


def test_ipv6_throttle_and_session_prefixes(monkeypatch):
    monkeypatch.setattr(
        "flo.kernel.session.client_address._settings", lambda: Settings(origin_shared_secret=SECRET)
    )

    def r(ip):
        return request({"X-FLO-Origin-Secret": SECRET, "X-FLO-Client-IP": ip})

    first = r("2001:db8:abcd:1234::1")
    assert throttle_client_value(first) == "2001:db8:abcd:1234::/64"
    assert throttle_client_value(first) == throttle_client_value(r("2001:db8:abcd:1234::2"))
    assert throttle_client_value(first) != throttle_client_value(r("2001:db8:abcd:1235::1"))
    assert request_device(first).ip_prefix == "2001:db8:abcd::/48"
    assert throttle_client_value(request({}, None)) == "unknown"


def test_spoof_regression_detects_unconditional_header_trust(monkeypatch):
    monkeypatch.setattr(
        "flo.kernel.session.client_address.trusted_client_host",
        lambda r: r.headers.get("x-flo-client-ip"),
    )
    with pytest.raises(AssertionError):
        assert throttle_client_value(request({"X-FLO-Client-IP": "203.0.113.2"})) == "198.51.100.8"
