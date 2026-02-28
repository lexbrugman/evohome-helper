from types import SimpleNamespace

from evohome_helper.func_tools import return_cache


def test_return_cache_retries_until_data_available(monkeypatch):
    state = SimpleNamespace(call_count=0, current_time=0)
    sleeps = []

    monkeypatch.setattr("evohome_helper.func_tools.time.time", lambda: state.current_time)
    monkeypatch.setattr("evohome_helper.func_tools.time.sleep", lambda seconds: sleeps.append(seconds))

    @return_cache(refresh_interval=1, max_retries=4, back_off=2, sleep=0.5)
    def load_data():
        state.call_count += 1
        if state.call_count < 3:
            state.current_time += 1
            return None
        return "ready"

    assert load_data() == "ready"
    assert state.call_count == 3
    assert sleeps == [1.0, 4.0]


def test_return_cache_reuses_cached_value_within_interval(monkeypatch):
    state = SimpleNamespace(current_time=200, calls=0)

    monkeypatch.setattr("evohome_helper.func_tools.time.time", lambda: state.current_time)

    @return_cache(refresh_interval=60)
    def source_data(value):
        state.calls += 1
        return {"value": value, "call": state.calls}

    first = source_data(1)
    second = source_data(1)

    assert first == second
    assert state.calls == 1

    state.current_time = 500
    third = source_data(1)

    assert third["call"] == 2
