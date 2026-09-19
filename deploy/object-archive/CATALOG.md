# Dynamic CardDeck catalog

`docker-compose.catalog-v3.yml` runs a read-only catalog service on the Garage
gateway's Tailnet address. It dynamically lists the explicitly configured
public buckets, inspects each `.carddeck` archive, and serves a JSON document
at `/packs/index`.

The service needs these untracked `.env` settings:

```text
GARAGE_TAILNET_IP=<gateway Tailnet address>
OBJECT_ARCHIVE_REGION=<region label>
CATALOG_UID=<owner UID of secrets/archive-indexer-key.txt>
CATALOG_GID=<owner GID of secrets/archive-indexer-key.txt>
CATALOG_PUBLIC_OBJECT_DOMAIN=<archive virtual-host domain>
```

`secrets/archive-indexer-key.txt` must be mode `0600` and must belong to the
configured UID/GID. Grant that key **read** access only to each bucket named by
`CATALOG_BUCKETS` (defaults to `bad-decisions-native,bad-decisions-pyx`). Do
not use the uploader or Garage admin credential for this service.

The catalog accepts only the fixed four-member CardDeck archive layout and
applies the same limits as the importer: archives over 5 MiB, members over
2 MiB, compression ratios over 100:1, symbolic links, encrypted members, and
unreadable or non-object JSON are listed under `rejected_archives` and never
fail the whole catalog. Its output includes each archive's SHA-256,
metadata, license/provenance, card counts, and direct public download URL.

## Caching and rate limiting

The service caches the encoded catalog in memory for `CATALOG_CACHE_TTL`
seconds (default 60; optional `.env` setting). Concurrent requests share one
storage scan, and a stale catalog is served briefly if storage is unreachable.

On the public nginx edge, install both templates:

1. Render `nginx-carddeck-catalog.http.conf.template` (set `@CATALOG_CACHE_DIR@`
   to a directory writable by nginx) into the `http` context, e.g. `conf.d/`,
   and reload nginx. It defines the `carddeck_catalog_rl` rate-limit zone and
   the `carddeck_catalog_cache` proxy cache.
2. Then render `nginx-carddeck-catalog.conf.template`, which applies
   `limit_req` (429 on excess) and a 60-second `proxy_cache` to `/packs/index`.
