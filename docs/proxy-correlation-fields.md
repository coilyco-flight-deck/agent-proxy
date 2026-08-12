# Correlation header and metadata fields

Part of [proxy.md](proxy.md). Accepted request headers, their OpenAI
`metadata` fallbacks, and the span attribute each becomes.

* `x-request-id` or `metadata.request_id` - `agentproxy.request_id`
* `x-ward-run-id` or `metadata.ward.run_id` - `ward.run_id`
* `x-ward-container-name` or `metadata.ward.container_name` - `ward.container_name`
* `x-ward-role` or `metadata.ward.role` - `ward.role`
* `x-ward-harness` or `metadata.ward.harness` - `ward.harness`
* `x-ward-target-repo` or `metadata.ward.target_repo` - `ward.target_repo`
* `x-ward-issue-ref` or `metadata.ward.issue_ref` - `ward.issue_ref`
* `x-ward-workflow` or `metadata.ward.workflow` - `ward.workflow`
* `x-ward-context-level` or `metadata.ward.context_level` - `ward.context_level`
* `x-ward-version` or `metadata.ward.version` - `ward.version`
* `x-agent-session-id` or `metadata.agent.session_id` - `agent.session_id`

Prometheus labels stay unchanged. The new correlation fields live only in logs
and traces.
