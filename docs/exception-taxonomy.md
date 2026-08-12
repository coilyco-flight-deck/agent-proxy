# Exception taxonomy

The closed set of runtime error codes Agent Proxy projects onto SigNoz Error
Management. Defined by `ERROR_TAXONOMY` in [`app/obs.py`](../app/obs.py).

## Grouping cardinality

Grouping is bounded by this table: **13 codes across 8 stages**, including the
`unclassified_error` fallback and its `unknown` stage. Nothing at runtime can
widen it. A code outside the table collapses to `unclassified_error` and the
requested value is discarded rather than passed through, because an unbounded
string reaching a span attribute is the exact failure the taxonomy prevents.

`tests/test_exception_taxonomy.py` walks every `record_error("...")` literal in
`app/` and fails if one is missing from the table, so a new code has to be
declared rather than invented at the call site.

## Fields

Each recorded error sets three things on its span:

- `error.type` - the stable machine key, and the SigNoz grouping key
- `error.stage` - which part of the request path failed
- the exception message and span status description - a fixed human-readable
  summary, never a dynamic diagnostic

The exception type is always `app.obs._RecordedError`. Dynamic diagnostics stay
in structured logs and non-grouping span attributes, so no prompt, response,
tool payload, credential, path, or upstream error text can enter an exception
field.

## Codes by stage

| Stage | Code | Summary |
| --- | --- | --- |
| `request` | `invalid_request_error` | Client request was malformed |
| `request` | `model_not_found` | Requested logical route is unknown |
| `request` | `model_unavailable` | Requested logical route is disabled |
| `request` | `rate_limit_error` | Request rejected by queue backpressure |
| `dispatch` | `response_validation_failed` | Upstream response failed validation |
| `dispatch` | `context_truncated` | Backend delivered less context than requested |
| `upstream` | `upstream_transport_failed` | Upstream backend transport failed |
| `upstream` | `upstream_error` | All backends failed for the request |
| `stream` | `stream_failed` | Streaming response failed before completion |
| `queue` | `queue_worker_failed` | Queue worker failed while dispatching |
| `capture` | `body_capture_failed` | Model body capture failed |
| `trajectory` | `trajectory_event_dropped` | Trajectory event dropped before storage |
| `trajectory` | `trajectory_event_persist_failed` | Trajectory event failed to persist |
| `unknown` | `unclassified_error` | Unclassified runtime error |

## Alerting notes

`error.stage` is the useful first cut. A `request` stage error is usually a
caller problem, while `upstream`, `dispatch`, and `stream` point at a backend.
`trajectory` and `capture` are cold-path and evidence problems that do not
affect the model response.

Recording happens at **attempt** granularity, so a retried attempt keeps its own
error span while the attempt that recovered stays clean. Alert on request
outcomes rather than raw exception counts, or a transient blip that the retry
absorbed will page. See [`proxy.md`](proxy.md#trace-correlation-metadata).

## See also

- [`proxy.md`](proxy.md) - the request path and where each stage sits.
