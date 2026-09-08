# Frontend runtime response contracts v1

## Purpose

TypeScript types do not validate data received over HTTP or WebSocket. The
dashboard therefore treats every successful JSON response as `unknown` until a
domain decoder verifies the fields used by the application. A malformed 2xx
response fails closed with a `ResponseContractError` that identifies the
contract and field path.

Binary exports and direct object-store uploads remain explicit exceptions:
their HTTP status, stream availability and required response headers are
validated by their dedicated transports.

## Transport invariant

The generic JSON transport requires a decoder for every call:

```text
fetch -> unknown JSON -> domain decoder -> typed value
```

It is not possible to add a new `api()` call without choosing a decoder because
the decoder is a required argument. Error responses are decoded only far enough
to expose an API detail message; they never enter a success domain model.

WebSocket mission-status messages use the same fail-closed decoder boundary
after JSON parsing. Invalid events are logged and do not mutate browser state.
Protocol messages `{"type":"ping"}` are consumed before the mission decoder;
the browser answers `{"type":"pong"}` on that same socket. Received pongs
refresh the server idle timer, while periodic authorization revalidation remains
independent and can still close a revoked session.

## Domain ownership

Validators are split by response ownership:

- authentication;
- mission catalogue, detail, parameters, pods, commands and status events;
- raster metadata, GeoJSON, analyses and saved styles;
- GCP collections, mutations, bundles, refresh and audit;
- multipart upload session, signed part, file completion, finalization and
  abort.

The shared decoder primitives validate objects, arrays, records, finite
numbers, enumerations, nullable values, tuples and GeoJSON geometry. Domain
validators accept additional response fields so the server can add compatible
metadata, while required fields and supported variants remain strict.

## Change policy

Backend response changes must update the corresponding frontend decoder and a
focused contract test in the same pull request. A change that makes an existing
required field optional must first make the browser behavior explicit; it must
not be hidden with an unchecked type assertion.

The browser E2E fixtures mirror complete server response shapes for the paths
they exercise. They remain an operator-journey metric, not scientific dataset
qualification.


## Private GSTile persistence

Persistent ranges are scoped by the authenticated organization, subject and role.
Anonymous views use memory only. Session initialization removes legacy unscoped
and other-identity databases before publishing the authenticated principal.
Logout and unauthorized responses close writers and purge GSTile databases;
BroadcastChannel (or storage events) propagates session changes to other tabs.
Requests from superseded session loads cannot restore the old principal.

Cleanup failure or missing database enumeration disables persistence for the
current page. A legacy tab that blocks deletion must be closed for its pending
IndexedDB deletion to finish; the application does not claim physical erasure
when the browser refuses it. This is application cache isolation, not encryption
against scripts already executing on the same origin or access to the OS profile.

Membership checks issue one indexed lookup per requested distinct key, in batches
of 256, and never enumerate all cache metadata. Local Chromium qualification
covered 600 stored ranges, identity separation, size mismatches, cancellation,
legacy cleanup, queued/late writers, and logout deletion across two tabs.
