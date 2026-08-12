# Trace correlation metadata

Part of [proxy](proxy.md).

## Trace correlation metadata


Each request can carry ward run metadata in headers, with OpenAI `metadata` as a
fallback when a client cannot set custom headers. The proxy copies the values
into `RequestTraceContext.extra`, structured logs, and span attributes so
SigNoz can join proxy traces with ward-run logs.

Every structured log emitted while an OpenTelemetry span is active also carries
the current lowercase hexadecimal `trace_id` and `span_id`. The ser8 SigNoz
`json-body` ingest pipeline promotes those fields from the retained JSON body,
which enables the traces-to-logs jump without duplicating log export in the
request path. Logs outside a valid span omit both fields.

Every handled request, stream, validation, transport, queue-worker, and
trajectory-persistence failure records an OpenTelemetry `exception` event and
marks its span as an error. The exception uses a static service-local type, a
closed-set human summary as its message, and `error.type` plus `error.stage`
attributes drawn from the bounded taxonomy in
[exception-taxonomy.md](exception-taxonomy.md). Dynamic diagnostics remain outside the
exception grouping event, giving the SigNoz Exceptions page complete baseline
coverage without adding new body capture.

`app.obs.record_error` is the single writer of that telemetry, so the event and
the `StatusCode.ERROR` status can never drift apart. Failure is recorded at
**attempt** granularity: a retried attempt and a dead backend each keep their own
error span, while the attempt that recovers or serves the request is left clean.
A transient blip therefore stays visible in Error Management without turning a
request that ultimately succeeded into a false alert. `tests/test_error_spans.py`
holds that contract for the non-streaming, streaming, retry-recovery,
backend-fallback, and redaction paths.

FastAPI instrumentation is installed while the application is assembled,
before Starlette freezes its middleware stack. The HTTP server span extracts
the caller's W3C `traceparent`, and `request.chat` or `request.completions`
continues beneath it. HTTPX then carries that same context into LiteLLM. Body
capture remains off unless `PROXY_TRACE_BODIES` is explicitly enabled. When it
is enabled, the request span owns the strict complete request and response
capture contract below.
