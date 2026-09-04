from tandem_gateway.cache import ResponseCache, cache_key, cacheable

CACHE_CFG = {"enabled": True, "max_temperature": 0.2}


def payload(**kwargs):
    body = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    body.update(kwargs)
    return body


def test_default_temperature_not_cacheable():
    body = payload()
    del body["temperature"]
    assert not cacheable(body, CACHE_CFG)


def test_low_temperature_cacheable():
    assert cacheable(payload(temperature=0), CACHE_CFG)
    assert not cacheable(payload(temperature=0.7), CACHE_CFG)


def test_stream_and_tools_not_cacheable():
    assert not cacheable(payload(stream=True), CACHE_CFG)
    assert not cacheable(payload(tools=[{}]), CACHE_CFG)


def test_key_depends_on_lane_and_content():
    body = payload()
    assert cache_key("small", body) != cache_key("large", body)
    other = payload()
    other["messages"] = [{"role": "user", "content": "bye"}]
    assert cache_key("small", body) != cache_key("small", other)


def test_ttl_expiry():
    cache = ResponseCache(max_entries=10, ttl_seconds=100)
    cache.put("k", {"v": 1}, now=0.0)
    assert cache.get("k", now=50.0) == {"v": 1}
    assert cache.get("k", now=150.0) is None


def test_lru_eviction():
    cache = ResponseCache(max_entries=2, ttl_seconds=1000)
    cache.put("a", 1, now=0)
    cache.put("b", 2, now=1)
    cache.get("a", now=2)      # refresh a
    cache.put("c", 3, now=3)   # evicts b
    assert cache.get("b", now=4) is None
    assert cache.get("a", now=4) == 1
    assert cache.get("c", now=4) == 3
