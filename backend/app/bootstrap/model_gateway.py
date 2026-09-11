from recruitment_ai_core.common.model_gateway import configure_model_gateway

from backend.app.infrastructure.model_gateway import OpenAICompatibleModelGateway


def configure_model_infrastructure() -> None:
    configure_model_gateway(OpenAICompatibleModelGateway())
