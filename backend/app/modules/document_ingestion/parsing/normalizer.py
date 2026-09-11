from __future__ import annotations

import re


class DocumentTextNormalizer:
    def normalize(self, text: str) -> str:
        value = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()
