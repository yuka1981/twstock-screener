import io

from twstock_screener.progress import ProgressReporter


def test_logs_every_n_in_non_tty_stream():
    stream = io.StringIO()
    p = ProgressReporter(total=10, label="job", stream=stream, log_every=5)
    for _ in range(10):
        p.update()
    p.close()
    out = stream.getvalue()
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 2
    assert "[5/10]" in lines[0]
    assert "[10/10]" in lines[1]
    assert "50.0%" in lines[0]
    assert "100.0%" in lines[1]


def test_suffix_appended_in_log_line():
    stream = io.StringIO()
    p = ProgressReporter(total=2, label="fetch", stream=stream, log_every=1)
    p.update(suffix="2330 ok")
    p.update(suffix="2317 fail")
    out = stream.getvalue()
    assert "2330 ok" in out
    assert "2317 fail" in out


def test_zero_total_does_not_divide_by_zero():
    stream = io.StringIO()
    p = ProgressReporter(total=0, label="empty", stream=stream, log_every=1)
    p.close()
    assert p.n == 0


def test_tty_stream_uses_carriage_return():
    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    stream = FakeTTY()
    p = ProgressReporter(total=3, label="x", stream=stream, log_every=10)
    p.update()
    p.update()
    p.update()
    p.close()
    out = stream.getvalue()
    assert "\r" in out
    assert out.endswith("\n")
