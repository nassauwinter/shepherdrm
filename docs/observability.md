# Observability

Shepherd RM emits structured JSON logs and exposes low-cardinality Prometheus
metrics on the API listener. Distributed tracing is not part of the initial
release.

## Scraping metrics

`GET /metrics` requires a bearer token belonging to an active Shepherd RM
administrator. Keep the API listener on an operator-controlled network even
though the endpoint is authenticated. Store the scrape token in a protected file
and configure Prometheus without placing it in command lines or URLs:

```yaml
scrape_configs:
  - job_name: shepherd-rm
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/shepherd_metrics_token
    static_configs:
      - targets: [shepherd-rm:8000]
```

Use a dedicated administrator identity and revocable API token for scraping.
The token must be distributed and rotated as an operator credential.

## Metrics

The initial metrics surface includes:

- request counts and latency by HTTP method, route template, and status class;
- allocation counts and latency by outcome and sharing mode;
- active leases and the share of active resources currently utilized;
- leases ended by expiration, overdue lease backlog, and oldest expiration lag;
- worker-loop success and failure totals, last-loop timestamps, and signal availability;
- build information containing the package version and source revision.

Metrics never label principals, resource or lease identifiers, resource labels,
token names, usernames, secret metadata, request bodies, or raw URL paths. An
unknown HTTP method is reported as `OTHER`, and unmatched routes use a fixed
marker.

## Alerts and diagnosis

Alert on sustained server-error rates or latency relative to the deployment's
own service objective. Allocation `unavailable` outcomes usually mean matching
capacity is exhausted or temporarily leased; investigate them together with
active leases and utilization rather than treating every occurrence as a server
failure.

Alert when `shepherd_rm_expiration_backlog_leases` remains above zero or
`shepherd_rm_expiration_lag_seconds` continues to rise beyond several configured
worker polling intervals. Also alert when `shepherd_rm_worker_metrics_available`
is zero or the successful-loop timestamp becomes stale. These patterns indicate
a stopped, failing, mismatched, or undersized expiration worker. Confirm the worker container is running, then
inspect its `expiration worker loop failed` and `expired leases` events. Database
unavailability appears as readiness failures, failed metric scrapes, and static
error events without database error text.

Structured request logs contain correlation IDs, route templates, status codes,
and durations. API and worker startup events identify the package version and
source revision. Secret values, authorization headers, raw paths, and exception
messages are deliberately excluded.

Worker totals are exchanged through the deployment's private atomic state file.
The file is not durable product data; replacing its volume resets the counters.
