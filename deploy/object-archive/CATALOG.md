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
`CATALOG_BUCKETS`. The service itself has no default and refuses to start
without it; only `docker-compose.catalog-v3.yml` supplies the default
`bad-decisions-native,bad-decisions-pyx`. Do not use the uploader or Garage
admin credential for this service.

The catalog accepts only the fixed four-member CardDeck archive layout. It
requires UTF-8 license and attribution documents, exact equality with their
metadata fields, and importer-equivalent control-character checks for printed
metadata and card text. It also applies the same limits as the importer:
archives over 5 MiB, members over 2 MiB, compression ratios over 100:1,
symbolic links, encrypted members, checksum or pack-ID mismatches, and
unreadable or non-object JSON are listed under `rejected_archives` and never
fail the whole catalog. `rejected_archives` means the archive's *content* is
invalid; storage or network errors (unreachable Garage, access denied,
timeouts) are never reported there. They fail the refresh instead, so a
partial outage cannot publish a catalog that wrongly rejects good archives.
Its output includes each archive's SHA-256,
metadata, license/provenance, card counts, and direct public download URL.

## Caching and rate limiting

The service caches the encoded catalog in memory for `CATALOG_CACHE_TTL`
seconds (default 60; optional `.env` setting). The value is validated once at
startup: a non-numeric, negative, or non-finite value stops the service with an
error in the container log instead of failing later per request. Concurrent
requests share one storage scan. If a refresh fails, the previous catalog is
served as stale and storage is retried after at most 10 seconds; with nothing
cached yet the service answers 503 and holds off retries for the same window.
Failures are logged to the container output.

Fresh responses carry `Cache-Control: public, max-age=<seconds left>`. Stale
responses (and any with under a second left) carry `no-cache`, so nginx and
browsers do not keep a stale catalog for a full TTL.

On the public nginx edge, install both templates:

1. Render `nginx-carddeck-catalog.http.conf.template` (set `@CATALOG_CACHE_DIR@`
   to a directory writable by nginx) into the `http` context, e.g. `conf.d/`,
   and reload nginx. It defines the `carddeck_catalog_rl` and
   `carddeck_archive_rl` rate-limit zones plus the `carddeck_catalog_cache`
   proxy cache. The archive proxy consumes the second zone and therefore must
   be installed only after this http-context file.
2. Then render `nginx-carddeck-catalog.conf.template`, which permits only GET
   and HEAD, caps request bodies at 1 MiB, and applies `limit_req` (429 on
   excess) plus a 60-second `proxy_cache` to `/packs/index`.
   The upstream `Cache-Control` header takes precedence over `proxy_cache_valid`,
   which only applies when the header is absent. Keep `proxy_cache_valid` in
   step with `CATALOG_CACHE_TTL` so the two agree if the header is ever dropped.

## Rebuilding the complete schema-2 collection

The audited source inventory is
`archive-inventory-v1.json`. It fixes the exact 46 source filenames, pack IDs,
and SHA-256 digests reviewed before the 2.0 migration. Rebuild into a new
directory; the tool refuses an existing destination and publishes nothing
unless every archive validates:

```bash
PYTHONPATH=src .venv/bin/python scripts/rebuild_carddecks_v2.py \
  build/archive-audit build/carddecks-v2 \
  --inventory deploy/object-archive/archive-inventory-v1.json \
  --replace packs_xkcdb.carddeck=build/xkcdb/xkcdb.carddeck
```

`rebuild-manifest.json` records source, selected-replacement, and output
digests plus IDs and card counts. Replacement archives must preserve the
source pack ID, card content, and order. The resulting filenames correspond
to Garage keys by replacing the first `packs_` with `packs/`.

Migrate only from a frozen copy of the current public catalog. The migration
helper is a credentialed dry-run unless `--apply` is present. It verifies
every live source digest, creates a non-`.carddeck` rollback object before
each replacement, verifies every upload, and restores all changed objects if
the run fails:

```bash
python scripts/migrate_carddeck_collection.py \
  catalog-before-v2.json build/carddecks-v2/rebuild-manifest.json \
  build/carddecks-v2 \
  --endpoint-url http://GARAGE_S3_API \
  --credentials /protected/archive-uploader-key.txt \
  --backup-prefix rollback/carddeck-v2-YYYYMMDD

# Repeat the identical validated command with --apply.
```

Keep the rollback prefix until the public schema-2 catalog and representative
remote imports have been verified.

## Rebuilding the legacy xkcdb archive

The repository does not bundle xkcdb card content. Given the owner-supplied
1.2.0 archive, reproduce the reviewed schema-2 archive with:

```bash
PYTHONPATH=src .venv/bin/python scripts/rebuild_xkcdb.py \
  SOURCE.carddeck build/xkcdb/xkcdb.carddeck
bad-decisions pack validate build/xkcdb/xkcdb.carddeck
sha256sum build/xkcdb/xkcdb.carddeck
```

The expected SHA-256 is
`f4cd71a43437bf9cebb460cecec8cc3efa34654ba7b45cba041c7be2463f82ba`.
The script changes terminology and makes the human-readable license and
attribution documents exactly match metadata; it asserts that all cards remain
unchanged. The pack declares `LicenseRef-XKCDB-Unspecified` and explicitly says
that no open-content license for the quote corpus was established. Upload or
redistribution therefore remains an owner/legal decision, not a right granted
by the Bad Decisions MIT license.
