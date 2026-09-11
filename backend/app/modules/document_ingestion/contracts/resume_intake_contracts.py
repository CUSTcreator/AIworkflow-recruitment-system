"""简历导入跨步骤合同。

约定：

1. ``SourceDocument`` 只保存原始 PDF；解析、结构化均属于一次
   ``ResumeSubmission``，同一 PDF 可以被重新解析。
2. Workflow 的 Step 和 Activity 只能传递这里定义的合同或带类型的
   ``WorkflowArtifact``，禁止透传实体 ``payload``、ORM 对象或页面 DTO。
3. 本文件中的模型 ``extra=forbid``。字段新增必须先升级合同版本，再修改生产者和
   消费者；这样字段错位会在边界被立即发现，而不会拖到下游评分阶段。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class _StrictContract(BaseModel):
    """所有跨步骤合同共享的严格校验和别名规则。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def artifact_json(self) -> dict[str, Any]:
        """以 WorkflowArtifact 约定的 camelCase 键写入当前版本检查点。"""
        return self.model_dump(mode="json", by_alias=True)


class ResumeMetadata(_StrictContract):
    """基础信息 LLM 结果；业务事实数组由发布层验证引用后消费。"""

    candidate_name: str = ""
    phone: str | None = None
    email: str | None = None
    current_title: str | None = None
    relevant_experience_years: float | None = None
    education_records: list[dict[str, Any]] = Field(default_factory=list)
    qualification_records: list[dict[str, Any]] = Field(default_factory=list)
    age: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResumeSourceSnapshot(_StrictContract):
    """步骤 1 冻结的唯一简历来源。

    其中的版本字段必须进入检查点：恢复中的 Workflow 不得因为部署升级或设置改动而
    偷偷改用新的解析/结构化规则。
    """

    schema_version: Literal["resume_source_snapshot_v1"] = "resume_source_snapshot_v1"
    submission_id: str = Field(validation_alias=AliasChoices("submissionId", "submission_id"), serialization_alias="submissionId")
    source_document_id: str = Field(validation_alias=AliasChoices("sourceDocumentId", "source_document_id"), serialization_alias="sourceDocumentId")
    object_ref: str = Field(validation_alias=AliasChoices("objectRef", "object_ref"), serialization_alias="objectRef")
    filename: str
    source_sha256: str = Field(validation_alias=AliasChoices("sourceSha256", "source_sha256"), serialization_alias="sourceSha256")
    intake_mode: str = Field(validation_alias=AliasChoices("intakeMode", "intake_mode"), serialization_alias="intakeMode")
    parse_strategy: Literal["reuse_verified_parse", "force_fresh_parse"] = Field(
        default="reuse_verified_parse",
        validation_alias=AliasChoices("parseStrategy", "parse_strategy"),
        serialization_alias="parseStrategy",
    )
    candidate_id: str | None = Field(default=None, validation_alias=AliasChoices("candidateId", "candidate_id"), serialization_alias="candidateId")
    name_override: str | None = Field(default=None, validation_alias=AliasChoices("nameOverride", "name_override"), serialization_alias="nameOverride")
    existing_text: str = Field(default="", validation_alias=AliasChoices("existingText", "existing_text"), serialization_alias="existingText")
    existing_metadata: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("existingMetadata", "existing_metadata"), serialization_alias="existingMetadata")
    existing_structure_ref: str | None = Field(default=None, validation_alias=AliasChoices("existingStructureRef", "existing_structure_ref"), serialization_alias="existingStructureRef")
    existing_structure_sha256: str | None = Field(default=None, validation_alias=AliasChoices("existingStructureSha256", "existing_structure_sha256"), serialization_alias="existingStructureSha256")
    existing_structure_schema_version: str | None = Field(default=None, validation_alias=AliasChoices("existingStructureSchemaVersion", "existing_structure_schema_version"), serialization_alias="existingStructureSchemaVersion")
    existing_structure_status: str | None = Field(default=None, validation_alias=AliasChoices("existingStructureStatus", "existing_structure_status"), serialization_alias="existingStructureStatus")
    existing_structure_metadata: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("existingStructureMetadata", "existing_structure_metadata"), serialization_alias="existingStructureMetadata")
    manual_correction_ref: str | None = Field(
        default=None,
        validation_alias=AliasChoices("manualCorrectionRef", "manual_correction_ref"),
        serialization_alias="manualCorrectionRef",
    )
    parser_version: str = Field(validation_alias=AliasChoices("parserVersion", "parser_version"), serialization_alias="parserVersion")
    structure_contract_version: str = Field(default="resume_structure_result_v3", validation_alias=AliasChoices("structureContractVersion", "structure_contract_version"), serialization_alias="structureContractVersion")


class DocumentParseResult(_StrictContract):
    """一次 Submission 的解析结果；原文在 Submission 列，块列表在对象存储。"""

    schema_version: Literal["document_parse_result_v1"] = "document_parse_result_v1"
    source: ResumeSourceSnapshot
    resume_text: str = Field(validation_alias=AliasChoices("resumeText", "resume_text"), serialization_alias="resumeText")
    parser_metadata: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("parserMetadata", "parser_metadata"), serialization_alias="parserMetadata")
    document_blocks_ref: str = Field(validation_alias=AliasChoices("documentBlocksRef", "document_blocks_ref"), serialization_alias="documentBlocksRef")
    document_blocks_sha256: str | None = Field(default=None, validation_alias=AliasChoices("documentBlocksSha256", "document_blocks_sha256"), serialization_alias="documentBlocksSha256")

    def persistence_json(self) -> dict[str, Any]:
        """不在 JSON 重复存原文；原文有独立的 ``parsed_text`` 列。"""
        return {
            "schemaVersion": self.schema_version,
            "parserVersion": self.source.parser_version,
            "sourceSha256": self.source.source_sha256,
            "documentBlocksRef": self.document_blocks_ref,
            "documentBlocksSha256": self.document_blocks_sha256,
            "metadata": self.parser_metadata,
        }


class ResumeStructureResult(_StrictContract):
    """结构化 Step 的已验证输出引用，不等同于已发布 ResumeProfile。"""

    schema_version: Literal["resume_structure_artifact_v1"] = "resume_structure_artifact_v1"
    parse: DocumentParseResult
    structure_ref: str = Field(validation_alias=AliasChoices("structureRef", "structure_ref"), serialization_alias="structureRef")
    structure_sha256: str = Field(validation_alias=AliasChoices("structureSha256", "structure_sha256"), serialization_alias="structureSha256")
    structure_schema_version: str = Field(validation_alias=AliasChoices("structureSchemaVersion", "structure_schema_version"), serialization_alias="structureSchemaVersion")
    structure_status: Literal["passed", "repaired"] = Field(validation_alias=AliasChoices("structureStatus", "structure_status"), serialization_alias="structureStatus")
    structure_method: str | None = Field(default=None, validation_alias=AliasChoices("structureMethod", "structure_method"), serialization_alias="structureMethod")
    structure_warnings: list[str] = Field(default_factory=list, validation_alias=AliasChoices("structureWarnings", "structure_warnings"), serialization_alias="structureWarnings")
    metadata: ResumeMetadata
    extraction_trace: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("extractionTrace", "extraction_trace"), serialization_alias="extractionTrace")

    def persistence_json(self) -> dict[str, Any]:
        """供 ResumeSubmission 保存可展示的结构化处理事实，不混入路由或页面字段。"""
        return {
            "schemaVersion": self.schema_version,
            "structureSchemaVersion": self.structure_schema_version,
            "structureStatus": self.structure_status,
            "structureMethod": self.structure_method,
            "structureWarnings": self.structure_warnings,
            "metadata": self.metadata.model_dump(mode="json"),
            "trace": self.extraction_trace,
        }


class ResumePublishPrepared(_StrictContract):
    """发布 Step 在事务外准备好的输入。

    ``structure`` 仍是算法包的完整结构化 JSON；在发布事务内必须恢复为
    ``VerifiedResumeIR`` 后才能创建 ResumeProfile，不能被当作任意页面数据写入。
    """

    schema_version: Literal["resume_publish_prepared_v1"] = "resume_publish_prepared_v1"
    structure_result: ResumeStructureResult = Field(
        validation_alias=AliasChoices("structureResult", "structure_result"),
        serialization_alias="structureResult",
    )
    structure: dict[str, Any]

class ResumeProfileSchema(_StrictContract):
    """已发布 ResumeProfile 的完整、可评分结构化事实。

    JSON 仍用于保存嵌套的经历、证据和技能列表，但顶层字段是固定合同；评分流程只能
    通过这个模型的白名单投影读取，不能再直接读取数据库 payload。
    """

    resume_profile_version: str
    resume_profile_version_id: str
    candidate_id: str
    resume_raw_sha256: str
    resume_redacted_sha256: str
    candidate_facts: dict[str, Any] = Field(default_factory=dict)
    skill_claims: list[dict[str, Any]] = Field(default_factory=list)
    experience_units: list[dict[str, Any]] = Field(default_factory=list)
    input_quality_report: dict[str, Any] | None = None
    structuring_provenance: dict[str, Any] = Field(default_factory=dict)
