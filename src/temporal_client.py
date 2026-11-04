"""Shared Client/Worker configuration for Temporal's official Agents integration."""

from datetime import timedelta

from agents.models.interface import Model, ModelProvider
from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin

TASK_QUEUE = "manual-translation"


class WorkerOpenAIProvider(ModelProvider):
    """Create credentials and a zero-retry OpenAI client only on model execution."""

    def __init__(self) -> None:
        self.provider: OpenAIProvider | None = None

    def get_model(self, model_name: str | None) -> Model:
        """Let Temporal own retries; missing credentials do not prevent worker startup."""
        if self.provider is None:
            self.provider = OpenAIProvider(
                openai_client=AsyncOpenAI(max_retries=0, timeout=240)
            )
        return self.provider.get_model(model_name)


def agents_plugin(model_provider: ModelProvider | None = None) -> OpenAIAgentsPlugin:
    """Configure retryable per-model Activities and automatic Pydantic conversion."""
    return OpenAIAgentsPlugin(
        model_provider=model_provider or WorkerOpenAIProvider(),
        model_params=ModelActivityParameters(
            start_to_close_timeout=timedelta(minutes=5),
            schedule_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(minutes=2),
                maximum_attempts=3,
            ),
        ),
    )


async def connect_temporal(address: str = "localhost:7233") -> Client:
    """Use the same official plugin on the CLI client and persistent Workers."""
    return await Client.connect(address, plugins=[agents_plugin()])
