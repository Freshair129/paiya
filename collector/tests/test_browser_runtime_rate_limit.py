from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import paiya_collector.browser_runtime as runtime
import pytest
from paiya_collector.browser_adapter import RateLimitPause, _raise_for_rate_limit
from paiya_collector.browser_runtime import PlaywrightVisiblePage


@pytest.mark.parametrize(
    ("first_header", "second_header", "expected_seconds"),
    [
        ("120", "1", 120),
        ("120", "180", 181),
        ("120", None, None),
        (None, "120", None),
        ("120", "Fri, 31 Dec 9999 23:59:59 -1200", None),
    ],
)
def test_multiple_rate_limits_never_shorten_or_guess_the_pause(
    monkeypatch, first_header, second_header, expected_seconds
):
    first = datetime(2026, 11, 13, 5, 0, tzinfo=UTC)
    times = iter([first, first + timedelta(seconds=1)])

    class Clock:
        @staticmethod
        def now(_timezone):
            return next(times)

    class Page:
        url = "https://www.facebook.com/groups/synthetic"

        def on(self, *_args):
            pass

    monkeypatch.setattr(runtime, "datetime", Clock)
    guarded = PlaywrightVisiblePage(Page(), Page.url)
    first_headers = {} if first_header is None else {"retry-after": first_header}
    guarded._on_response(SimpleNamespace(status=429, headers=first_headers))
    headers = {} if second_header is None else {"retry-after": second_header}
    guarded._on_response(SimpleNamespace(status=429, headers=headers))
    with pytest.raises(RateLimitPause) as caught:
        _raise_for_rate_limit(guarded.snapshot({}), now=first)
    if expected_seconds is None:
        assert caught.value.manual_resume_required
    else:
        assert caught.value.retry_after_until == first + timedelta(seconds=expected_seconds)
