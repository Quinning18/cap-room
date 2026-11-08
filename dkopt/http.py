import time
import requests

_session = requests.Session()


def get_json(url, params=None, retries=3, timeout=30):
    last = None
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:  # retry transient failures
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed: {last}")
