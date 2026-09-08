# Mission catalogue refresh

The authenticated workspace polls `/missions/revisions` every 30 seconds while
visible. This owner/organization-scoped index contains one opaque revision per
visible mission, the server observation time and the configured stale threshold.
A revision includes the mission ID/update timestamp and every main-pipeline stage
ID/update timestamp. Stage-only changes, non-maximum timestamps, new attempts and
stage removal therefore invalidate the row. Analysis-only stages do not contribute
to the main mission projection. Writers must retain the existing `updated_at`
contract; direct SQL bypassing timestamps is outside this contract.

The client requests only new/changed rows through `POST /missions/catalog-items`
with 1–100 IDs (1–256 characters each). This read-only endpoint uses the same owner
and tenant policy as the catalogue, including explicit audited administrator
delegation. An absent/unauthorized ID returns no item. POST keeps the bounded
selection out of potentially oversized URLs. `/missions` remains compatible and
now eagerly loads stage collections instead of issuing one query per mission.

A complete revision index removes deleted/no-longer-visible rows. Missing bulk
results are retried next poll; partial failures never commit new client revisions.
Index and item requests are not one database snapshot: a concurrent commit may
need the next poll to converge, but no timestamp watermark permanently skips a
late commit. Concurrent refreshes coalesce. Cache state lives only in the current
authenticated runtime, is discarded on identity change and is never persisted.
Unknown WebSocket missions invalidate the index; the selected mission still gets
a fresh detail request. Freshness ages advance using server time even when no row
changes. Time alone never changes a pipeline result into failure.

## Validation and limits

The synthetic SQLite baseline with 20,000 missions (no stages) used 200 HTTP pages,
20,400 SQL queries and 7,098,176 raw JSON bytes per full refresh. The revision route
uses two SQL queries, one response (1,648,969 raw JSON bytes) and no item downloads
on an unchanged warm refresh. The observed synthetic elapsed times were 7.806 s
before and 0.076 s for the index; they are not production latency guarantees. Cold loading still fetches all rows in batches of 100. The index remains
O(missions + stage metadata), including its memory and serialization costs; this
is not an O(changes) server journal or a production throughput qualification.

Regression tests cover tenant/owner scope, HTTP bounds, stage-only/backdated
changes, deletion/recreation, bounded eager query counts, 20k client rows,
unchanged/changed refresh, retry, partial failure, cancellation and identity reset.
The existing HTTP/PostgreSQL integration checks creation/cancellation revisions;
the existing browser suite covers loading beyond the first 100 missions.
