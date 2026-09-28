# Make a CardDeck in five minutes

This directory is an original, CC0 example with one prompt and three answers.
Edit `pack.json`, replacing the example identity, cards, attribution, license,
and provenance with truthful values for your work.

Install the engine and build the archive:

```bash
pipx install bad-decisions
cp -R examples/minimal-pack /tmp/my-questionable-pack
cd /tmp/my-questionable-pack
./build.sh
bad-decisions pack validate dist/example-pack.carddeck
```

`bad-decisions pack export` generates `manifest.json`, calculates the exact
pack checksum, and copies the metadata's license notice and attribution into
the archive. The resulting archive contains exactly:

```text
manifest.json
pack.json
LICENSE.txt
ATTRIBUTION.md
```

Import it into an absolute, empty registry:

```bash
mkdir /tmp/bad-decisions-registry
bad-decisions pack import "$PWD/dist/example-pack.carddeck" /tmp/bad-decisions-registry
BAD_DECISIONS_PACK_DIR=/tmp/bad-decisions-registry bad-decisions --oneshot
```

Before sharing a pack, read the [CardDeck specification](../../docs/CARDDECK.md).
Card content is a separate work from the MIT-licensed engine: choose a license
you have the right to apply, record the real creator and source, and preserve
required attribution.
