from __future__ import annotations

import os
from pathlib import Path


os.environ["RECRUIT_LLM_CONFIG_PATH"] = str(
    Path(__file__).with_name("llm.disabled.json")
)
