# Bad Decisions Object Archive

This is a single-node Garage deployment template for public, virtual-hosted
object URLs such as:

```text
https://<bucket>.objects.<region>.<domain>/<object-key>
```

Garage listens only on the Docker host's Tailnet address. A separate public
nginx host terminates TLS and proxies requests to Garage's `s3_web` endpoint
without changing the `Host` header, so the bucket name remains available to
Garage.

`replication_factor = 1` has no storage redundancy. Back up Garage's metadata,
data, and snapshots outside this host.

Before deployment, render `garage.toml.template` with the Tailnet address,
region, and object-archive domain; create `secrets/rpc_secret`,
`secrets/admin_token`, and `secrets/metrics_token` with mode `0600`; and create
an untracked `.env` containing `GARAGE_TAILNET_IP`.
