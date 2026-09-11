class LLMCallError(RuntimeError):
    """A model provider call could not be completed."""


class LLMResponseError(RuntimeError):
    """A model provider response could not satisfy the output contract."""