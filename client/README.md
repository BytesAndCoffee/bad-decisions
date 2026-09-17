# Bad Decisions terminal client

`bad-decisions-client` is a dependency-free terminal client for a Bad
Decisions service. It contains no card corpus and runs on Linux, macOS, and
Windows.

```bash
python -m pip install bad-decisions-client
regret health
regret deal
regret deal --packs base,maha
regret --list-packs
```

The default endpoint is `https://bytes.coffee/bad-decisions`. Use `--api-url`
for another compatible deployment. Connection and API failures return a
non-zero exit status.

## Release

Build and validate before uploading a new immutable PyPI version:

```bash
cd client
python -m build
twine check dist/*
```
