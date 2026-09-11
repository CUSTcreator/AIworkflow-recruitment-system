from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from backend.app.core.config import settings
from recruitment_ai_core.llm import load_llm_settings

from .contracts import ParseCandidate, ParseComparison, ParseDecision
from .document_blocks import block_text
from .mineru_client import MinerUClient, MinerUPollResult, MinerUResult, MinerUSubmission
from .normalizer import DocumentTextNormalizer
from .quality_evaluator import DocumentQualityEvaluator
from .recovery_service import LocalPdfRecoveryService
from .result_comparator import ParseResultComparator
from .vision_llm_provider import VisionLLMProvider


# 解析器选择及 Block 合并规则变化后必须升级，避免复用旧的整份 VLM 结果。
PARSER_VERSION = "document_parser_v4"


@dataclass(slots=True)
class DocumentParseResult:
    text: str
    metadata: dict[str, Any]
    blocks: list[dict[str, Any]] | None = None


class DocumentParsingPipeline:
    def __init__(
        self,
        *,
        mineru: MinerUClient | None = None,
        local_recovery: LocalPdfRecoveryService | None = None,
        vision: VisionLLMProvider | None = None,
        quality: DocumentQualityEvaluator | None = None,
        normalizer: DocumentTextNormalizer | None = None,
        comparator: ParseResultComparator | None = None,
    ) -> None:
        document_config = load_llm_settings().document_parsing
        mineru_config = _mapping(document_config.get("mineru"))
        vision_config = _mapping(document_config.get("vision_llm"))
        comparison_config = _mapping(document_config.get("comparison"))
        self.mineru = mineru or MinerUClient(
            base_url=str(mineru_config.get("base_url") or "https://mineru.net"),
            api_key=str(mineru_config.get("api_key") or ""),
            model_version=str(mineru_config.get("model_version") or "vlm"),
            timeout_seconds=int(
                mineru_config.get("timeout_seconds")
                or settings.document_parse_timeout_seconds
            ),
            request_timeout_seconds=int(
                mineru_config.get("request_timeout_seconds") or 30
            ),
            enable_formula=bool(mineru_config.get("enable_formula", False)),
            enable_table=bool(mineru_config.get("enable_table", True)),
            language=str(mineru_config.get("language") or "ch"),
            max_result_bytes=int(
                mineru_config.get("max_result_bytes") or 100 * 1024 * 1024
            ),
        )
        self.vision = vision or VisionLLMProvider(
            enabled=bool(vision_config.get("enabled", False)),
            base_url=str(vision_config.get("base_url") or ""),
            api_key=str(vision_config.get("api_key") or ""),
            model=str(vision_config.get("model") or ""),
            timeout_seconds=int(vision_config.get("timeout_seconds") or 180),
            dpi=int(vision_config.get("dpi") or 144),
            max_pages=int(vision_config.get("max_pages") or 20),
            enable_thinking=bool(
                vision_config.get("enable_thinking", False)
            ),
            response_format=str(
                vision_config.get("response_format") or "json_object"
            ),
        )
        self.local_recovery = local_recovery or LocalPdfRecoveryService()
        self.quality = quality or DocumentQualityEvaluator()
        self.normalizer = normalizer or DocumentTextNormalizer()
        self.comparator = comparator or ParseResultComparator(comparison_config)
        self.fallback_to_local = bool(
            document_config.get("fallback_to_local", True)
        )

    @property
    def vision_configured(self) -> bool:
        return self.vision.configured

    def uses_async_mineru(self) -> bool:
        """当前配置需要 MinerU 时，工作流以 batchId 分两次执行而非同步轮询。"""
        return (
            settings.document_parser_backend.strip().lower() in {"auto", "mineru"}
            and self.mineru.configured
        )

    def submit_mineru(
        self,
        data: bytes,
        filename: str,
        *,
        data_id: str | None,
        timeout_seconds: int,
        idempotency_key: str,
    ) -> MinerUSubmission:
        return self.mineru.submit(
            data,
            filename,
            data_id=data_id,
            is_ocr=self._needs_ocr(data),
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    def poll_mineru(
        self,
        *,
        batch_id: str,
        filename: str,
        data_id: str | None,
        timeout_seconds: int,
        idempotency_key: str,
    ) -> MinerUPollResult:
        return self.mineru.poll_once(
            MinerUSubmission(batch_id=batch_id, filename=filename, data_id=data_id),
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    def complete_mineru(
        self,
        data: bytes,
        filename: str,
        poll: MinerUPollResult,
        *,
        timeout_seconds: int,
        idempotency_key: str,
    ) -> DocumentParseResult:
        """用完成的 MinerU 结果执行本地质量对比与必要的视觉兜底。"""
        result = self.mineru.download_completed(
            poll,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )
        return self._select_completed_mineru(
            data,
            filename,
            result,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    def _select_completed_mineru(
        self,
        data: bytes,
        filename: str,
        result: MinerUResult,
        *,
        timeout_seconds: int,
        idempotency_key: str,
    ) -> DocumentParseResult:
        local, local_attempt = self._try_local(data)
        mineru = self._evaluate(
            ParseCandidate(
                provider="mineru",
                text=self.normalizer.normalize(result.text),
                blocks=result.blocks,
                page_count=result.page_count or (local.page_count if local else None),
                metadata=result.metadata,
            ),
            expected_page_count=local.page_count if local else None,
            reference_blocks=local.blocks if local else None,
        )
        return self._select_candidates(
            data,
            filename,
            mineru=mineru,
            local=local,
            attempts=[_attempt(mineru), local_attempt],
            backend=settings.document_parser_backend.strip().lower(),
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )
    def parse_without_mineru(
        self,
        data: bytes,
        filename: str,
        *,
        timeout_seconds: int | None = None,
        idempotency_key: str | None = None,
    ) -> DocumentParseResult:
        """执行显式的非 MinerU 兜底，禁止绕过可恢复的三段式调用。

        配置了 MinerU 时只能由 Workflow 调用 submit/poll/complete，使 Worker
        重启后仍能根据 batch_id 继续。这里只有强制本地模式或 auto 模式下
        MinerU 未配置时才可进入。
        """

        budget = max(
            1,
            int(timeout_seconds or getattr(self.mineru, "timeout_seconds", settings.document_parse_timeout_seconds)),
        )

        backend = settings.document_parser_backend.strip().lower()
        if backend not in {"auto", "mineru", "local"}:
            raise RuntimeError(f"unsupported_document_parser_backend:{backend}")

        local, local_attempt = self._try_local(data)
        if backend == "local":
            if local is None or not local.quality or not local.quality.accepted:
                raise RuntimeError("document_parse_quality_rejected:local_pymupdf")
            return self._result(
                local,
                ParseDecision("local_pymupdf", ["local_backend_forced"]),
                [local_attempt],
                None,
            )

        if backend == "mineru":
            raise RuntimeError("mineru_not_configured")
        if self.mineru.configured:
            raise RuntimeError("async_mineru_workflow_required")
        return self._select_candidates(
            data,
            filename,
            mineru=None,
            local=local,
            attempts=[
                {
                    "provider": "mineru",
                    "accepted": False,
                    "error": "mineru_not_configured",
                },
                local_attempt,
            ],
            backend=backend,
            timeout_seconds=budget,
            idempotency_key=idempotency_key,
        )

    def _select_candidates(
        self,
        data: bytes,
        filename: str,
        *,
        mineru: ParseCandidate | None,
        local: ParseCandidate | None,
        attempts: list[dict[str, Any]],
        backend: str,
        timeout_seconds: int,
        idempotency_key: str | None,
    ) -> DocumentParseResult:
        """Apply the single parser-selection policy after sources are collected.

        A valid MinerU result is the document skeleton. Cross-parser differences
        are diagnostics, not permission to discard every MinerU block and bbox.
        Whole-document fallback is reserved for an unavailable or rejected
        MinerU result.
        """

        mineru_valid = bool(mineru and mineru.quality and mineru.quality.accepted)
        local_valid = bool(local and local.quality and local.quality.accepted)
        if backend == "mineru":
            if not mineru_valid or mineru is None:
                raise RuntimeError("mineru_parse_quality_rejected")
            return self._result(
                mineru,
                ParseDecision("mineru", ["mineru_backend_forced"]),
                attempts,
                None,
            )

        if mineru_valid and mineru:
            comparison = (
                self.comparator.compare(mineru, local)
                if local_valid and local
                else None
            )
            if comparison is None:
                reasons = ["local_text_layer_unreliable"]
            elif comparison.consistent:
                reasons = ["mineru_and_text_layer_consistent"]
                if comparison.warnings:
                    reasons.append("mineru_selected_with_comparison_warnings")
            else:
                reasons = [
                    "parser_results_conflict",
                    "mineru_selected_with_quality_warning",
                ]
            return self._result(
                mineru,
                ParseDecision("mineru", reasons),
                attempts,
                comparison,
            )

        page_repair_attempted = False
        if mineru and local_valid and local and self.vision.configured:
            missing_pages = self._repairable_missing_pages(mineru, local)
            if missing_pages:
                page_repair_attempted = True
                repaired, repair_attempt = self._try_repair_mineru_pages(
                    data,
                    filename,
                    mineru=mineru,
                    local=local,
                    page_numbers=missing_pages,
                    timeout_seconds=timeout_seconds,
                    idempotency_key=idempotency_key,
                )
                attempts.append(repair_attempt)
                if repaired and repaired.quality and repaired.quality.accepted:
                    comparison = self.comparator.compare(repaired, local)
                    return self._result(
                        repaired,
                        ParseDecision(
                            "mineru",
                            ["mineru_missing_pages_repaired_with_vision"],
                            vlm_triggered=True,
                            vlm_trigger_reason="mineru_missing_page_content",
                        ),
                        attempts,
                        comparison,
                    )

        if local_valid and local and self.fallback_to_local:
            if (
                local.quality
                and local.quality.layout_risk
                and self.vision.configured
                and not page_repair_attempted
            ):
                return self._vision_result(
                    data,
                    filename,
                    attempts,
                    None,
                    trigger_reason="mineru_failed_and_local_layout_risky",
                    text_layer=local,
                    timeout_seconds=timeout_seconds,
                    idempotency_key=idempotency_key,
                )
            return self._result(
                local,
                ParseDecision(
                    "local_pymupdf",
                    [
                        "mineru_unavailable_or_rejected",
                        "local_text_layer_valid",
                        *(
                            ["vision_page_repair_failed_without_full_retry"]
                            if page_repair_attempted
                            else []
                        ),
                    ],
                ),
                attempts,
                None,
            )
        if self.vision.configured:
            return self._vision_result(
                data,
                filename,
                attempts,
                None,
                trigger_reason="mineru_and_local_rejected",
                text_layer=None,
                timeout_seconds=timeout_seconds,
                idempotency_key=idempotency_key,
            )
        errors = [
            str(item.get("error") or ",".join(item.get("quality_reasons") or []))
            for item in attempts
            if item.get("error") or item.get("quality_reasons")
        ]
        raise RuntimeError(
            "document_parse_quality_rejected:"
            + (";".join(errors) if errors else "no_valid_parser_result")
        )

    @staticmethod
    def _repairable_missing_pages(
        mineru: ParseCandidate,
        local: ParseCandidate,
    ) -> list[int]:
        if not mineru.quality or "missing_page_content" not in mineru.quality.reasons:
            return []
        mineru_pages = {
            int(block["page"])
            for block in mineru.blocks
            if isinstance(block.get("page"), int)
            and str(block.get("text") or "").strip()
        }
        local_text_by_page: dict[int, str] = {}
        for block in local.blocks:
            page = block.get("page")
            value = str(block.get("text") or "").strip()
            if isinstance(page, int) and value:
                local_text_by_page[page] = (
                    local_text_by_page.get(page, "") + "\n" + value
                ).strip()
        return [
            page
            for page, value in sorted(local_text_by_page.items())
            if page not in mineru_pages and len(value) >= 80
        ]

    def _try_repair_mineru_pages(
        self,
        data: bytes,
        filename: str,
        *,
        mineru: ParseCandidate,
        local: ParseCandidate,
        page_numbers: list[int],
        timeout_seconds: int,
        idempotency_key: str | None,
    ) -> tuple[ParseCandidate | None, dict[str, Any]]:
        repair_key = (
            hashlib.sha256(
                f"{idempotency_key}:vision-page-repair:{page_numbers}".encode("utf-8")
            ).hexdigest()
            if idempotency_key
            else None
        )
        try:
            vision = self.vision.parse(
                data,
                filename,
                timeout_seconds=timeout_seconds,
                idempotency_key=repair_key,
                page_numbers=page_numbers,
            )
            returned_pages = set(vision.metadata.get("returned_pages") or [])
            missing_after_repair = [
                page for page in page_numbers if page not in returned_pages
            ]
            if missing_after_repair:
                raise RuntimeError(
                    "vision_page_repair_incomplete:"
                    + ",".join(str(page) for page in missing_after_repair)
                )
            repaired_blocks = [dict(block) for block in mineru.blocks]
            repaired_blocks.extend(dict(block) for block in vision.blocks)
            repaired_blocks.sort(
                key=lambda block: (
                    int(block.get("page") or 10**9),
                    int(block.get("order") or 0),
                )
            )
            for order, block in enumerate(repaired_blocks, start=1):
                block["block_id"] = f"B_{order:04d}"
                block["order"] = order
            candidate = self._evaluate(
                ParseCandidate(
                    provider="mineru",
                    text=self.normalizer.normalize(block_text(repaired_blocks)),
                    blocks=repaired_blocks,
                    page_count=mineru.page_count or local.page_count,
                    metadata={
                        **mineru.metadata,
                        "base_provider": "mineru",
                        "repair_mode": "page_patch",
                        "repaired_pages": page_numbers,
                        "vision_page_repair": vision.metadata,
                    },
                ),
                expected_page_count=local.page_count,
                reference_blocks=local.blocks,
            )
            return candidate, _attempt(candidate)
        except Exception as exc:
            return None, {
                "provider": "vision_llm_page_repair",
                "accepted": False,
                "pages": page_numbers,
                "error": f"{type(exc).__name__}:{str(exc)[:240]}",
            }

    def _try_local(
        self, data: bytes
    ) -> tuple[ParseCandidate | None, dict[str, Any]]:
        try:
            text, page_count, blocks = self.local_recovery.extract(data)
            candidate = self._evaluate(
                ParseCandidate(
                    provider="local_pymupdf",
                    text=self.normalizer.normalize(text),
                    blocks=blocks,
                    page_count=page_count,
                    metadata={"provider": "local_pymupdf"},
                ),
                expected_page_count=page_count,
            )
            return candidate, _attempt(candidate)
        except Exception as exc:
            return None, {
                "provider": "local_pymupdf",
                "accepted": False,
                "error": f"{type(exc).__name__}:{str(exc)[:240]}",
            }

    def _vision_result(
        self,
        data: bytes,
        filename: str,
        attempts: list[dict[str, Any]],
        comparison: ParseComparison | None,
        *,
        trigger_reason: str,
        text_layer: ParseCandidate | None,
        timeout_seconds: int,
        idempotency_key: str | None = None,
    ) -> DocumentParseResult:
        try:
            candidate = self._vision_candidate(data, filename, timeout_seconds=timeout_seconds, idempotency_key=idempotency_key)
        except Exception as exc:
            if text_layer and text_layer.quality and text_layer.quality.accepted:
                return self._result(
                    text_layer,
                    ParseDecision(
                        "local_pymupdf",
                        [
                            trigger_reason,
                            "vision_llm_failed",
                            "validated_text_layer_fallback",
                        ],
                        vlm_triggered=True,
                        vlm_trigger_reason=trigger_reason,
                    ),
                    [
                        *attempts,
                        {
                            "provider": "vision_llm",
                            "accepted": False,
                            "error": f"{type(exc).__name__}:{str(exc)[:240]}",
                        },
                    ],
                    comparison,
                )
            raise
        attempts = [*attempts, _attempt(candidate)]
        if text_layer and text_layer.quality and text_layer.quality.accepted:
            vision_comparison = self.comparator.compare(candidate, text_layer)
            critical_fact_failed = (
                vision_comparison.critical_token_recall
                < self.comparator.critical_token_threshold
                or vision_comparison.numeric_token_recall
                < self.comparator.numeric_token_threshold
            )
            if not vision_comparison.consistent:
                validation_reason = (
                    "vision_llm_critical_fact_validation_failed"
                    if critical_fact_failed
                    else "vision_llm_text_layer_validation_failed"
                )
                return self._result(
                    text_layer,
                    ParseDecision(
                        "local_pymupdf",
                        [
                            trigger_reason,
                            validation_reason,
                            "validated_text_layer_fallback",
                        ],
                        vlm_triggered=True,
                        vlm_trigger_reason=trigger_reason,
                    ),
                    attempts,
                    comparison,
                )
            candidate.metadata["text_layer_comparison"] = (
                vision_comparison.to_dict()
            )
        return self._result(
            candidate,
            ParseDecision(
                "vision_llm",
                [trigger_reason, "vision_llm_validation_passed"],
                vlm_triggered=True,
                vlm_trigger_reason=trigger_reason,
            ),
            attempts,
            comparison,
        )

    def _vision_candidate(
        self, data: bytes, filename: str, *, timeout_seconds: int, idempotency_key: str | None = None
    ) -> ParseCandidate:
        candidate = self.vision.parse(data, filename, timeout_seconds=timeout_seconds, idempotency_key=idempotency_key)
        if candidate.metadata.get("missing_pages"):
            # A whole-document fallback has no trusted base for absent pages.
            # Partial responses are only safe when explicitly patching a base.
            raise RuntimeError("vision_llm_parse_quality_rejected:missing_pages")
        candidate.text = self.normalizer.normalize(candidate.text)
        candidate = self._evaluate(
            candidate, expected_page_count=candidate.page_count
        )
        if not candidate.quality or not candidate.quality.accepted:
            reasons = (
                candidate.quality.reasons
                if candidate.quality
                else ["quality_report_missing"]
            )
            raise RuntimeError(
                "vision_llm_parse_quality_rejected:" + ",".join(reasons)
            )
        return candidate

    def _evaluate(
        self,
        candidate: ParseCandidate,
        *,
        expected_page_count: int | None,
        reference_blocks: list[dict[str, Any]] | None = None,
    ) -> ParseCandidate:
        candidate.quality = self.quality.evaluate(
            candidate.text,
            page_count=candidate.page_count,
            expected_page_count=expected_page_count,
            blocks=candidate.blocks,
            reference_blocks=reference_blocks,
        )
        return candidate

    def _result(
        self,
        selected: ParseCandidate,
        decision: ParseDecision,
        attempts: list[dict[str, Any]],
        comparison: ParseComparison | None,
    ) -> DocumentParseResult:
        quality = selected.quality
        metadata = {
            **selected.metadata,
            "provider": selected.provider,
            "parser_version": PARSER_VERSION,
            "page_count": selected.page_count,
            "quality_score": quality.score if quality else 0.0,
            "quality_report": quality.to_dict() if quality else {},
            "comparison": comparison.to_dict() if comparison else None,
            "parse_decision": decision.to_dict(),
            "attempts": attempts,
        }
        if selected.provider == "mineru":
            metadata.setdefault("base_provider", "mineru")
            metadata.setdefault("repair_mode", "none")
            metadata.setdefault("repaired_pages", [])
        elif selected.provider in {"vision_llm", "local_pymupdf"}:
            metadata.setdefault("base_provider", selected.provider)
            metadata.setdefault(
                "repair_mode",
                "none"
                if "local_backend_forced" in decision.reasons
                else "full_fallback",
            )
        return DocumentParseResult(
            text=selected.text,
            blocks=selected.blocks,
            metadata=metadata,
        )

    @staticmethod
    def _needs_ocr(data: bytes) -> bool:
        try:
            import fitz

            document = fitz.open(stream=data, filetype="pdf")
            try:
                sample_pages = min(3, document.page_count)
                embedded_text = "".join(
                    document[index].get_text("text")
                    for index in range(sample_pages)
                )
                return len(embedded_text.strip()) < max(
                    40, sample_pages * 20
                )
            finally:
                document.close()
        except Exception:
            return True


def _attempt(candidate: ParseCandidate) -> dict[str, Any]:
    quality = candidate.quality
    return {
        "provider": candidate.provider,
        "accepted": bool(quality and quality.accepted),
        "quality_score": quality.score if quality else 0.0,
        "quality_reasons": list(quality.reasons) if quality else [],
        "quality_warnings": list(quality.warnings) if quality else [],
        "character_count": quality.character_count if quality else 0,
        "text_sha256": hashlib.sha256(
            candidate.text.encode("utf-8")
        ).hexdigest(),
        "duration_ms": candidate.metadata.get("duration_ms"),
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
