# The reliability proxy (phase 1)

This is the walkthrough for the phase-1 reliability proxy built per aosh leg
`04-headless-proxy-build.md` against the locked leg-02 architecture. It covers
the request path, the `app/` modules, configuration, how to run it, and how to
prove the core `num_ctx` fix. The design is locked upstream and not re-argued
here - see the source-of-truth pointers in the README.

The walkthrough is split across the pages below so each stays inside the
repository documentation caps. This page keeps the map and the anchors other
documents already link to.

## Contents

- [Request path and endpoints](proxy-request-path.md)
- [Trace correlation metadata](proxy-trace-correlation.md)
- [Correlation header and metadata fields](proxy-correlation-fields.md)
- [Model I/O capture contract](proxy-capture-contract.md)
- [SigNoz content viewing contract](proxy-signoz-viewing.md)
- [Capture projections](proxy-capture-projections.md)
- [Response validation](proxy-validation.md)
- [Upstream error classification](upstream-error-classification.md)
- [Prompt cache accounting](proxy-prompt-cache.md)
- [Configuration](proxy-configuration.md)
- [Auto num_ctx and the NUM_PARALLEL coupling](proxy-num-ctx.md)
- [Running, proving, and metrics](proxy-operations.md)

## Moved sections

The headings below are kept so links written before the split still resolve.
Each one names the page that now holds the content.

## Trace correlation metadata

Moved to [proxy-trace-correlation.md](proxy-trace-correlation.md).

### SigNoz content viewing contract

Moved to [proxy-signoz-viewing.md](proxy-signoz-viewing.md).

## Validation

Moved to [proxy-validation.md](proxy-validation.md).

### Removed: the self-verification claim check

Moved to [proxy-validation.md](proxy-validation.md).
