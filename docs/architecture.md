# Architecture

The system is an event-driven asyncio pipeline:
`DataStage -> SignalStage -> RiskStage -> ExecutionStage -> AuditStage`.
