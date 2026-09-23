# Bundled card packs

The JSON files in this directory are card-pack content, not Bad Decisions
software. They are separate works and are not automatically covered by the
engine's MIT License merely because the wheel bundles and loads them.

Each pack's `metadata` is authoritative for that pack. It records the declared
license identifier and notice, optional license URL, authors and attribution,
source/provenance records, and modifications. Those terms must travel with the
pack and continue to govern its use. BytesAndCoffee's distribution of a pack
does not grant, broaden, or reinterpret rights beyond the pack's own license.

The loader reads only `*.json`; this document is packaged alongside the default
packs so the software/content boundary remains visible in installed wheels.
