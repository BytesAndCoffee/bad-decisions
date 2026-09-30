# Consequences analytics and feedback

Consequences is the optional analytics system for Bad Decisions. It is disabled
unless an operator configures a persistent store, and Regret keeps its client
side disabled until a user opts in.

## Privacy boundary

Consequences records route templates, method, status, duration, optional random
client/session UUIDs, and authoritative drawn-card provenance. It does not
record IP addresses, user agents, raw query strings, raw request headers, or
feedback capability tokens. Feedback tokens are returned only to the client,
stored server-side as verifiers, and expire after seven days.

Regret controls:

```bash
regret consequences status
regret consequences enjoy
regret consequences regret
regret identity reset
regret identity off
regret feedback enjoy
regret feedback regret
regret feedback clear
```

The first interactive deal asks once when no preference is stored;
non-interactive use never opts in automatically.

The hosted one-shot browser asks whether to enable **card voting** before it
sends a Consequences identity. Enabling it permits Enjoy/Regret votes and
stores a random pseudonymous identifier in that browser. The browser then sends
that identifier and a per-page session ID with every request, so the votes, the
drawn cards, and the request details described above are recorded against
them. Continuing without voting sends neither votes nor either identifier. The footer's
**Voting & privacy preference** control reopens the choice without requiring
site-storage cleanup.

## Server configuration

Use a local persistent SQLite path owned by the service account:

```bash
BAD_DECISIONS_CONSEQUENCES_DB=/var/lib/bad-decisions/consequences.sqlite3
```

Do not place the database in an immutable release, object store, or shared
filesystem. Dealing remains available if analytics fails. Feedback can be
disabled independently with `BAD_DECISIONS_CONSEQUENCES_FEEDBACK=0`; public
combination summaries require the explicit
`BAD_DECISIONS_CONSEQUENCES_PUBLIC_STATS=1` setting.

## Owner reports

```bash
bad-decisions consequences report
bad-decisions consequences tui
bad-decisions consequences rebuild /absolute/path/consequences.sqlite3
bad-decisions consequences purge /absolute/path/consequences.sqlite3 --retention-days 90
```

The Textual dashboard provides summary, combination, prompt, answer, and recent
draw views with sortable drill-downs. These commands are owner tools; raw
analytics are not exposed by the public API.
