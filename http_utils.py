"""urlopen with certifi's CA bundle scoped to each request.

Framework Python 3.12 venvs ship without a usable system CA chain, which
breaks urllib HTTPS calls (Codex quota, GLM quota, sensor HTTP). yfinance
relies on requests/certifi and is unaffected — this evens that out without
touching the global ssl default context.
"""

from urllib.request import HTTPSHandler, build_opener


def https_urlopen(request, timeout: float):
    try:
        import certifi
        import ssl
        context = ssl.create_default_context(cafile=certifi.where())
        opener = build_opener(HTTPSHandler(context=context))
    except ImportError:
        opener = build_opener()
    return opener.open(request, timeout=timeout)
