"""Legacy package retained only for non-production supporting data contracts.

Post-interview scoring is now entered through the backend seven-step Workflow and
``interview_evaluation`` package.  The former parser/pipeline entry point was
removed so the old parallel-list flow cannot be called accidentally.
"""

__all__: list[str] = []
