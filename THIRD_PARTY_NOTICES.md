# Third-party notices

Heliograph uses the following open-source components at run time. Each
remains under its own license. Development-only tools (linters, type checker,
test runner) are not distributed and are not listed here.

<!-- licence-report:start -->
| Component | Version | Licence | Type | Project |
| --- | --- | --- | --- | --- |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 | permissive | https://github.com/aio-libs/aiohappyeyeballs |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | permissive | https://github.com/aio-libs/aiohttp |
| aiohttp-fast-zlib | 0.3.0 | Apache-2.0 | permissive | https://github.com/bluetooth-devices/aiohttp-fast-zlib |
| aiosignal | 1.4.0 | Apache-2.0 | permissive | https://github.com/aio-libs/aiosignal |
| annotated-types | 0.8.0 | MIT | permissive | https://github.com/annotated-types/annotated-types |
| attrs | 26.1.0 | MIT | permissive |  |
| ccxt | 4.5.89 | MIT | permissive | https://ccxt.com |
| certifi | 2026.10.19 | MPL-2.0 | copyleft | https://github.com/certifi/python-certifi |
| cffi | 2.0.0 | MIT | permissive | https://github.com/python-cffi/cffi |
| charset-normalizer | 3.4.7 | MIT | permissive |  |
| coincurve | 21.0.0 | MIT OR Apache-2.0 | permissive | https://github.com/ofek/coincurve |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | permissive | https://github.com/pyca/cryptography/ |
| frozenlist | 1.8.0 | Apache-2.0 | permissive | https://github.com/aio-libs/frozenlist |
| idna | 3.18 | BSD-3-Clause | permissive | https://github.com/kjd/idna |
| msgspec | 0.22.0 | BSD-3-Clause | permissive | https://github.com/msgspec/msgspec |
| multidict | 6.9.1 | Apache-2.0 | permissive | https://github.com/aio-libs/multidict |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | copyleft | https://github.com/ijl/orjson |
| propcache | 0.5.2 | Apache-2.0 | permissive | https://github.com/aio-libs/propcache |
| pyarrow | 26.0.0 | Apache-2.0 | permissive | https://github.com/apache/arrow |
| pycparser | 3.0 | BSD-3-Clause | permissive | https://github.com/eliben/pycparser |
| pydantic | 2.14.1 | MIT | permissive | https://github.com/pydantic/pydantic |
| pydantic-core | 2.50.1 | MIT | permissive | https://github.com/pydantic/pydantic-core |
| requests | 2.34.2 | Apache-2.0 | permissive | https://github.com/psf/requests |
| typing-extensions | 4.16.0 | PSF-2.0 | permissive | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | permissive | https://github.com/pydantic/typing-inspection |
| urllib3 | 2.8.0 | MIT | permissive |  |
| uvloop | 0.22.1 | Apache-2.0 | permissive |  |
| winloop | 0.6.3 | MIT | permissive | https://github.com/Vizonex/Winloop |
| yarl | 1.25.1 | Apache-2.0 | permissive | https://github.com/aio-libs/yarl |
| zlib-ng | 1.0.0 | PSF-2.0 | permissive | https://github.com/pycompression/python-zlib-ng |
<!-- licence-report:end -->

The table is generated from `uv.lock` and each package's metadata by
`python3 scripts/licence_report.py --write`; `./scripts/check.sh` fails when
it is out of date or a licence breaks the policy in `docs/licensing.md`.
