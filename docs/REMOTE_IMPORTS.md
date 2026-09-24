# Remote CardDeck imports

Bad Decisions can safely import public CardDeck archives without granting the
runtime any ability to mutate packs. Remote imports are an explicit CLI action;
restart the runtime with `BAD_DECISIONS_PACK_DIR` after importing.

Import one archive:

```bash
bad-decisions pack import --remote \
  https://bad-decisions-native.objects.us-west-1.bytes.coffee/packs/coffee.carddeck \
  /absolute/pack/registry
```

Import selected packs from the live public catalog:

```bash
bad-decisions pack import --index \
  https://bad-decisions.objects.us-west-1.bytes.coffee/packs/index \
  /absolute/pack/registry \
  --pack coffee \
  --pack pyx-2-base-game-us
```

Only HTTPS URLs are accepted. Redirects, credentials in URLs, oversized
downloads, malformed index entries, duplicate selections, and archive URLs not
ending in `.carddeck` are rejected. Downloaded archives pass the same strict
validation, checksum, non-overwrite, and atomic-write import behavior as local
files.
