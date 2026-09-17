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
refuses archives larger than 8 MiB. Its output includes each archive's SHA-256,
metadata, license/provenance, card counts, and direct public download URL.
