# Third-party notices

Heliograph uses the following open-source components at run time. Each
remains under its own license. Development-only tools (linters, type checker,
test runner) are not distributed and are not listed here.

<!-- licence-report:start -->
| Component | Version | Licence | Type | Project |
| --- | --- | --- | --- | --- |
| annotated-types | 0.8.0 | MIT | permissive | https://github.com/annotated-types/annotated-types |
| msgspec | 0.22.0 | BSD-3-Clause | permissive | https://github.com/msgspec/msgspec |
| pydantic | 2.14.1 | MIT | permissive | https://github.com/pydantic/pydantic |
| pydantic-core | 2.50.1 | MIT | permissive | https://github.com/pydantic/pydantic-core |
| typing-extensions | 4.16.0 | PSF-2.0 | permissive | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | permissive | https://github.com/pydantic/typing-inspection |
<!-- licence-report:end -->

The table is generated from `uv.lock` and each package's metadata by
`python3 scripts/licence_report.py --write`; `./scripts/check.sh` fails when
it is out of date or a licence breaks the policy in `docs/licensing.md`.
