# CardDeck 1: portable card-pack format

CardDeck is a small, offline interchange format for card packs compatible with
fill-in-the-blank card-game engines. A CardDeck archive is a ZIP file named
with the `.carddeck` extension. This document defines version 1.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** are to be interpreted as described by RFC 2119.

## Goals

- One file contains a validated card pack and its attribution/license text.
- An implementation can inspect or validate it without network access.
- The archive cannot silently change a running service; importing is an
  explicit local administrator action.
- Stable card identities and provenance survive sharing between registries.

## Archive layout

A conforming version-1 archive MUST contain exactly these four regular files at
its ZIP root. Directory entries and additional files are not allowed.

```text
manifest.json
pack.json
LICENSE.txt
ATTRIBUTION.md
```

Member names MUST be plain basenames. They MUST NOT contain path separators,
`.` or `..` path components, or symbolic links. `LICENSE.txt` and
`ATTRIBUTION.md` MUST be non-empty UTF-8 text.

## Manifest

`manifest.json` MUST be UTF-8 JSON conforming to
[`schemas/carddeck-manifest-v1.schema.json`](schemas/carddeck-manifest-v1.schema.json).
It has no extension fields in version 1.

```json
{
  "format": "carddeck",
  "format_version": 1,
  "pack_id": "example-pack",
  "pack_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

`pack_sha256` is the lowercase hexadecimal SHA-256 digest of the exact,
uncompressed bytes in `pack.json`; it is not a digest of re-serialized JSON.
`pack_id` MUST equal `pack.json`'s `metadata.id` and match
`^[a-z0-9][a-z0-9_-]*$`. The ID `all` is reserved.

## Pack payload

`pack.json` is UTF-8 JSON with `schema_version: 1`. It contains:

```text
schema_version
metadata
black
white
```

`metadata` MUST include a stable ID, name, description, version, language,
custom flag, authors, attribution, license ID, license notice, sources, and
modifications. It MAY include `license_url`.

Each black card has a unique ID, `repr`, `template`, positive integer `slots`,
and its pack ID. Its template MAY use only anonymous `{}` fields and escaped
`{{`/`}}`; the number of anonymous fields MUST equal `slots`. Each white card
has a unique ID, non-empty `text`, and its pack ID. A pack MUST contain at least
one card. Card IDs are unique across both colors. Implementations SHOULD retain
optional `source_ref` values as provenance without treating them as executable
or network-resolvable references.

The pack metadata's attribution and license notice describe the cards, while
the adjacent files provide the human-readable material needed to redistribute
them. Pack authors are responsible for having the rights necessary to share
their card text and source references.

### Licensing boundary

A CardDeck pack is a separate work from any engine that creates, validates,
imports, or reads it. Compatibility with Bad Decisions does not place a pack
under the engine's MIT License, and the engine's license grants no additional
rights to third-party card content.

Within `metadata`, `license_id` identifies the pack's declared license,
`license_url` MAY link to that license, `authors` and `attribution` identify the
creator and required credit, and `sources` records provenance through an
`origin` URL or descriptive source reference plus optional edition, digest,
retrieval date, and license evidence. These fields, the pack's
`license_notice`, its modification history, and the adjacent human-readable
files govern redistribution and use of the pack. A distributor or engine
publisher MUST NOT override, broaden, or reinterpret third-party content rights
merely by packaging the pack with MIT-licensed software.

## Validation and import

A conforming importer MUST, before writing anything:

1. Reject a missing, malformed, unsupported, or checksum-mismatched manifest.
2. Reject an archive whose member set differs from the required layout.
3. Reject invalid pack data, mismatched pack IDs, empty license/attribution
   files, unsafe paths, symlinks, or duplicate card IDs.
4. Enforce documented size and decompression limits to mitigate ZIP bombs.

An importer MUST NOT overwrite an existing `<pack_id>.json` without an
explicit separate overwrite policy. It SHOULD write to a temporary file in the
target registry and atomically replace only that temporary file into place.

The reference implementation limits each member to 2 MiB, the archive payload
to 5 MiB, and a member's compression ratio to 100:1. Other implementations MAY
choose stricter limits but SHOULD document them.

Importing a CardDeck archive does not require, authorize, or imply a runtime API
upload. A server SHOULD load its registry only at startup and require an
operator-controlled restart after its registry changes.

## Export

Exporters SHOULD emit only the four required files and SHOULD use a stable JSON
serialization for generated `pack.json`. They MUST calculate `pack_sha256`
from the actual bytes they place in the archive. Deterministic ZIP metadata is
recommended but is not required for conformance.

## Compatibility

Consumers MUST reject `format_version` values they do not understand. A future
CardDeck versions may add capabilities only through a new version; version-1
readers MUST NOT silently accept unknown archive members or manifest fields.

## Reference commands

```bash
bad-decisions pack validate example.carddeck
bad-decisions pack export example ./example.carddeck
bad-decisions pack init-registry /absolute/pack/registry
bad-decisions pack import example.carddeck /absolute/pack/registry
```
