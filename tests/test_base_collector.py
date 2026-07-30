import asyncio

import pytest

from monitoring_agent.domain.entities import CollectorResponse, CollectorStatus, Resource, ResourceType
from monitoring_agent.infrastructure.adapters.base_collector import BaseCollector


class FlakyCollector(BaseCollector):
    def __init__(self) -> None:
        super().__init__(name="flaky-collector", timeout_seconds=0.01, max_retries=2, retry_backoff_seconds=0.0)
        self.attempts = 0

    async def collect(self) -> CollectorResponse:
        return await self._execute_with_retry(self._collect_once, operation_name="collect")

    async def validate(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    async def _collect_once(self) -> CollectorResponse:
        self.attempts += 1
        if self.attempts < 3:
            raise asyncio.TimeoutError("simulated timeout")

        return CollectorResponse(
            collector_name=self.name,
            status=CollectorStatus.SUCCESS,
            resources=[Resource(resource_id="res-1", name="demo", type=ResourceType.SERVICE)],
        )


@pytest.mark.asyncio
async def test_base_collector_retries_and_succeeds() -> None:
    collector = FlakyCollector()

    response = await collector.collect()

    assert response.status is CollectorStatus.SUCCESS
    assert collector.attempts == 3
    assert response.collector_name == "flaky-collector"
