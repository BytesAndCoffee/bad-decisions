# Cards Against Coffee terminal client

`cards-against-coffee` is a dependency-free terminal client for the Cards
Against Coffee API. It includes no card corpus and does not cache rounds.

```bash
python -m pip install cards-against-coffee
coffee-cards
coffee-cards --packs base,maha
coffee-cards --black-packs maha --white-packs base,maha
coffee-cards --list-packs
coffee-cards --json
```

The default API is `https://bytes.coffee/cah`. Override it with
`--api-url http://127.0.0.1:8000` for a private or local deployment.

Card-data licensing and adult-content constraints are set by the service and
its packs, not by this client.

## Release to PyPI

`cards-against-coffee` was available on PyPI when prepared; recheck immediately
before publishing.

```bash
cd client
python -m pip install --upgrade build twine
python -m build
twine check dist/*
twine upload dist/*
```

Use a PyPI API token through `TWINE_USERNAME=__token__` and `TWINE_PASSWORD`,
or configure a keyring/token locally. Do not commit credentials or `.pypirc`.
