"""权限目录与招聘操作的授权声明。

本文件是后端权限码的唯一静态来源。授权规则分为四类：数据范围、敏感业务
动作、系统管理身份和可见数据上的纯恢复操作。角色、用户覆盖和接口都引用
这里的权限码；它不负责读取数据库，也不执行授权判断。管理端职责包通过
管理接口投影，浏览器不应长期维护另一份权限组合枚举。

新增已登录操作必须先在这里归类，再接入 Router、Service 或 CommandRunner：
申请上的读取、材料查看和纯恢复继承申请可见性；申请上的敏感动作必须声明
职责权限及必要负责人限制；资源无关的业务操作声明权限与目标部门；系统管理
操作使用系统管理员身份。不得在接口中自行创造未登记的动作名称或角色判断。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


AssignmentRequirement = Literal["first_interviewer", "hr"]
# 普通业务动作必须命中具体权限码；visible_retry 只复用当前申请的可见性，
# 适用于不会推进招聘主状态、不会提交人工业务结论的失败恢复操作。
ApplicationAuthorizationMode = Literal["business_permission", "visible_retry"]
ResumeSubmissionAuthorizationMode = Literal[
    "basic_view", "material_view", "recovery", "sensitive_permission"
]


@dataclass(frozen=True, slots=True)
class ApplicationActionRequirement:
    """一个 Application 动作的职责要求及可选负责人限制。

    ``authorization_policy.can_application_action`` 会先要求申请可见，因此
    此处声明的是在可见性成立后额外需要的职责，而不是可绕过可见性的权限。
    """

    # 纯失败恢复只继承申请可见性，不属于职责包，因此没有原子权限码。
    permission_code: str | None = None
    assignment: AssignmentRequirement | None = None
    authorization_mode: ApplicationAuthorizationMode = "business_permission"
    # ``run_scoring`` 首次执行仍是敏感操作；仅失败后的重新执行属于纯恢复。
    visibility_retry_statuses: frozenset[str] = frozenset()
    # 同一个状态机动作可能被多个阶段复用；接口级动作必须声明自己可出现的
    # 申请状态，防止调用其他阶段的接口绕过页面动作过滤。
    allowed_statuses: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ResumeSubmissionActionRequirement:
    """简历处理操作的统一授权分类。

    basic_view 与 material_view 都只要求记录可见；recovery
    重放已冻结的自动链路；sensitive_permission 会修改业务数据，必须再检查
    对应的业务权限。
    """

    authorization_mode: ResumeSubmissionAuthorizationMode
    permission_code: str | None = None


@dataclass(frozen=True, slots=True)
class ResponsibilityBundleDefinition:
    """管理员可配置的一项招聘职责。

    权限码仍是接口授权的最小单位，而职责包只是管理端的配置单位。将两者
    分开后，管理员不需要理解某个流程按钮背后的每一个技术权限码，后端也
    仍可对推进、淘汰和删除等敏感动作进行精确校验。
    """

    code: str
    label: str
    description: str
    permission_codes: frozenset[str]
    # 仅当职责包内全部原子权限都要求组织范围时才填写。混合范围职责包由
    # 原子权限元数据分别过滤，避免一个包中的岗位编辑被文档导入权限连带限制。
    required_business_scope: Literal["organization"] | None = None


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    """一个后端原子权限及其最低数据范围要求。

    职责包只是管理端的组合；真正执行授权时始终使用这里的原子权限码。
    ``required_business_scope`` 只表示该权限的最低范围，不替代目标资源的
    部门范围检查。
    """

    code: str
    label: str
    required_business_scope: Literal["organization"] | None = None


PERMISSION_LABELS: dict[str, str] = {
    "resume.upload": "上传简历",
    "resume_submission.manage": "处理简历导入",
    "application.create": "创建候选申请",
    "job_document.upload": "上传岗位 JD",
    "job_document.confirm": "确认岗位并创建",
    "job.edit": "编辑岗位",
    "job.delete": "删除岗位",
    "hard_screening.policy.manage": "配置岗位硬筛规则",
    "hard_screening.catalog.manage": "管理全局硬筛条件库",
    "interview_guide.manage": "管理通用面试题单",
    "screening.run": "运行初步筛选评分",
    "hard_screening.review": "人工复核硬筛",
    "department_review.manage": "执行部门审核",
    "application.delete": "删除岗位申请",
    "candidate_document.upload": "上传候选人文件",
    "candidate_document.manage": "管理候选人文件",
    "first_interview.manage": "管理技术一面",
    "second_interview.manage": "管理 HR 二面",
    "final_decision.manage": "最终招聘决策",
}


# 原子权限的范围约束独立于职责包。岗位文档和全局配置影响全公司，其他
# 部门业务权限仍可由本部门范围角色使用。授权策略会自动读取这份目录。
PERMISSION_DEFINITIONS: dict[str, PermissionDefinition] = {
    code: PermissionDefinition(
        code=code,
        label=label,
        required_business_scope=(
            "organization"
            if code in {
                "job_document.upload",
                "job_document.confirm",
                "hard_screening.catalog.manage",
                "interview_guide.manage",
            }
            else None
        ),
    )
    for code, label in PERMISSION_LABELS.items()
}


PERMISSION_REQUIRED_BUSINESS_SCOPE: dict[str, Literal["organization"]] = {
    code: definition.required_business_scope
    for code, definition in PERMISSION_DEFINITIONS.items()
    if definition.required_business_scope is not None
}


# 这是账号和角色管理端可操作的唯一职责目录。每一项对应一组业务动作；
# 页面展示的是职责包，接口授权仍按展开后的原子权限逐项判断。
RESPONSIBILITY_BUNDLES: dict[str, ResponsibilityBundleDefinition] = {
    "candidate_materials": ResponsibilityBundleDefinition(
        code="candidate_materials",
        label="候选人材料",
        description="导入简历并处理候选人资料",
        permission_codes=frozenset({
            "resume.upload", "resume_submission.manage", "application.create",
            "candidate_document.upload", "candidate_document.manage",
        }),
    ),
    "job_and_screening_policy": ResponsibilityBundleDefinition(
        code="job_and_screening_policy",
        label="岗位与筛选配置",
        # 混合范围职责只在页面说明一次范围差异，避免把原子操作标签重复堆叠。
        description="维护岗位和岗位级硬筛规则",
        permission_codes=frozenset({
            "job_document.upload", "job_document.confirm", "job.edit",
            "hard_screening.policy.manage",
        }),
    ),
    "department_recruitment_execution": ResponsibilityBundleDefinition(
        code="department_recruitment_execution",
        label="部门招聘执行",
        description="执行初筛、部门审核和技术一面",
        permission_codes=frozenset({
            "screening.run", "hard_screening.review", "department_review.manage",
            "first_interview.manage",
        }),
    ),
    "second_interview_management": ResponsibilityBundleDefinition(
        code="second_interview_management",
        label="HR 二面管理",
        description="组织 HR 二面、提交面评并推进流程",
        permission_codes=frozenset({"second_interview.manage"}),
    ),
    "final_recruitment_decision": ResponsibilityBundleDefinition(
        code="final_recruitment_decision",
        label="最终招聘决策",
        description="发放 offer 或结束招聘流程",
        permission_codes=frozenset({"final_decision.manage"}),
    ),
    "global_recruitment_configuration": ResponsibilityBundleDefinition(
        code="global_recruitment_configuration",
        label="全局招聘配置",
        description="维护全局硬筛条件库和通用面试题单",
        permission_codes=frozenset({
            "hard_screening.catalog.manage", "interview_guide.manage",
        }),
        required_business_scope="organization",
    ),
    "delete_business_records": ResponsibilityBundleDefinition(
        code="delete_business_records",
        label="删除岗位和申请",
        description="删除岗位或单个岗位申请",
        permission_codes=frozenset({"job.delete", "application.delete"}),
    ),
}


# 旧版本页面曾使用这些职责名称。数据库只保存展开后的原子权限，兼容别名
# 只用于读取和写入过渡；新接口返回的职责目录只包含上面的规范名称。
RESPONSIBILITY_BUNDLE_ALIASES: dict[str, str | None] = {
    "job_document_import_management": "job_and_screening_policy",
    "screening_and_department_decision": "department_recruitment_execution",
    "first_interview_management": "department_recruitment_execution",
    "recruitment_analytics": None,
}


def canonical_responsibility_bundle_code(code: str) -> str | None:
    """将历史职责名称转换为规范名称；已移除的分析职责返回 ``None``。"""

    if code in RESPONSIBILITY_BUNDLES:
        return code
    if code in RESPONSIBILITY_BUNDLE_ALIASES:
        return RESPONSIBILITY_BUNDLE_ALIASES[code]
    raise KeyError(code)


def canonical_responsibility_bundle_codes(
    bundle_codes: Iterable[str],
) -> list[str]:
    """规范化职责包并去重，忽略仅为兼容保留的已移除分析职责。"""

    normalized: list[str] = []
    for raw_code in bundle_codes:
        code = canonical_responsibility_bundle_code(str(raw_code))
        if code is not None and code not in normalized:
            normalized.append(code)
    return normalized


def expand_responsibility_bundles(
    bundle_codes: Iterable[str],
    *,
    business_scope: str | None = None,
) -> set[str]:
    """将管理端职责包展开为接口继续使用的原子权限码。

    调用方负责把未知职责包转换为业务校验错误；这里抛出 KeyError，可以避免
    未定义的包被静默忽略而意外授予空权限。
    """
    permissions: set[str] = set()
    for code in canonical_responsibility_bundle_codes(bundle_codes):
        permissions.update(RESPONSIBILITY_BUNDLES[code].permission_codes)
    if business_scope == "department":
        permissions.difference_update(PERMISSION_REQUIRED_BUSINESS_SCOPE)
    return permissions


def responsibility_bundle_codes_for_permissions(
    permission_codes: Iterable[str],
    *,
    business_scope: str | None = None,
) -> list[str]:
    """返回被完整授予的职责包，用于展示历史原子权限配置的兼容投影。"""
    granted = set(permission_codes)
    result: list[str] = []
    for code, bundle in RESPONSIBILITY_BUNDLES.items():
        expected = set(bundle.permission_codes)
        if business_scope == "department":
            expected.difference_update(PERMISSION_REQUIRED_BUSINESS_SCOPE)
        # 一个全公司专属职责在部门范围下没有可用原子权限，不能被误投影为已启用。
        if expected and expected <= granted:
            result.append(code)
    return result


def role_permissions_for_bundles(
    bundle_codes: Iterable[str],
    *,
    business_scope: str | None = None,
) -> set[str]:
    """生成角色的持久化原子权限集。

    数据范围不再被编码为“查看候选人”原子权限。账号可见数据完全由账号有效
    状态和 ``business_scope`` 决定；此函数只保存职责包展开后的敏感操作权限。
    """
    return expand_responsibility_bundles(bundle_codes, business_scope=business_scope)


ROLE_DEFAULT_RESPONSIBILITY_BUNDLES: dict[str, frozenset[str]] = {
    # HR 负责候选材料、岗位配置、二面和最终决策，但不默认承担本部门的
    # 初筛、部门审核和一面。需要时应由管理员显式授予部门招聘执行职责包。
    "hr": frozenset({
        "candidate_materials",
        "job_and_screening_policy",
        "second_interview_management",
        "final_recruitment_decision",
        "global_recruitment_configuration",
        "delete_business_records",
    }),
    "department_recruiter": frozenset({
        "candidate_materials",
        "department_recruitment_execution",
    }),
    "department_manager": frozenset(),
}


# 兼容尚未关联 RoleDefinition 的历史账号。新角色和新账号通过职责包保存，
# 但运行时仍只消费这一组原子权限码。
ROLE_DEFAULT_PERMISSIONS: dict[str, set[str]] = {
    role: role_permissions_for_bundles(
        bundle_codes,
        business_scope="organization" if role == "hr" else "department",
    )
    for role, bundle_codes in ROLE_DEFAULT_RESPONSIBILITY_BUNDLES.items()
}


APPLICATION_ACTION_REQUIREMENTS: dict[str, ApplicationActionRequirement] = {
    "run_scoring": ApplicationActionRequirement(
        "screening.run", visibility_retry_statuses=frozenset({"screening_failed"})
    ),
    "retry_initial_assessment": ApplicationActionRequirement(
        authorization_mode="visible_retry"
    ),
    "retry_hard_screening": ApplicationActionRequirement(
        authorization_mode="visible_retry"
    ),
    # 岗位来源恢复会重建画像或改变申请冻结的 JD，仍要求岗位编辑职责。
    "repair_job_profile": ApplicationActionRequirement("job.edit"),
    # 修改岗位硬筛规则与普通岗位信息编辑是两个原子能力。恢复入口必须与
    # 硬筛规则读写接口使用同一个权限，否则页面会下发点击后必然 403 的动作。
    "repair_hard_screening_policy": ApplicationActionRequirement(
        "hard_screening.policy.manage"
    ),
    # 人工硬筛和部门审核都会改变流程结论，但属于两个不同阶段；不能再用
    # “推进/淘汰候选人”这类跨阶段权限，以免角色获得未展示的业务能力。
    "review_hard_screening_pass": ApplicationActionRequirement("hard_screening.review"),
    "review_hard_screening_reject": ApplicationActionRequirement("hard_screening.review"),
    # 决定是否进入一面是部门审核结论。一面权限只负责题单、执行、面评和结论。
    # “推进到一面”仍是部门审核结论，不应提前要求操作者同时是一面负责人。
    # 从题单生成开始的一面写操作才应用 first_interviewer 负责人限制。
    "approve_first_interview": ApplicationActionRequirement("department_review.manage"),
    # 这三个名称是接口授权动作，不是新的原子权限。阶段内的通过、不通过、
    # 暂缓和人工复核统一复用该阶段已有的 manage 权限；状态机动作仍可保持
    # reject/hold/manual_review/offer，避免把每个按钮膨胀成可配置权限项。
    "department_decision": ApplicationActionRequirement(
        "department_review.manage",
        allowed_statuses=frozenset({"department_review"}),
    ),
    "run_first_interview_planning": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    "confirm_first_guide": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    # 自动题单生成失败后，允许同一面试官以可追溯的人工题纲继续一面。
    "continue_first_interview_manually": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    "start_first_interview": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    "save_first_interview_progress": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    "finish_first_interview": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    "submit_first_feedback": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    "complete_first_interview": ApplicationActionRequirement("first_interview.manage", "first_interviewer"),
    # 重新计算复用已冻结的面评，不重新提交面评或推进 Application 主状态。
    "retry_post_first_scoring": ApplicationActionRequirement(
        authorization_mode="visible_retry"
    ),
    # 修改面评会产生新的业务记录，必须由本轮负责人执行，不能复用纯计算重试的宽权限。
    "edit_first_interview_feedback": ApplicationActionRequirement(
        "first_interview.manage", "first_interviewer"
    ),
    # 上游 V1 缺失或拓扑损坏时，在后续面试阶段新建不可变 V1 版本，不回退申请状态。
    "rebuild_screening_assessment": ApplicationActionRequirement(
        authorization_mode="visible_retry"
    ),
    "approve_second_interview": ApplicationActionRequirement("second_interview.manage", "hr"),
    "hr_decision": ApplicationActionRequirement(
        "second_interview.manage",
        "hr",
        allowed_statuses=frozenset({"hr_second_review"}),
    ),
    "start_second_interview": ApplicationActionRequirement("second_interview.manage", "hr"),
    "save_second_interview_progress": ApplicationActionRequirement("second_interview.manage", "hr"),
    "finish_second_interview": ApplicationActionRequirement("second_interview.manage", "hr"),
    "submit_second_feedback": ApplicationActionRequirement("second_interview.manage", "hr"),
    "complete_second_interview": ApplicationActionRequirement("second_interview.manage", "hr"),
    "retry_post_second_scoring": ApplicationActionRequirement(
        authorization_mode="visible_retry"
    ),
    "edit_second_interview_feedback": ApplicationActionRequirement(
        "second_interview.manage", "hr"
    ),
    "final_decision": ApplicationActionRequirement(
        "final_decision.manage",
        "hr",
        allowed_statuses=frozenset({"final_review"}),
    ),
    "delete_application": ApplicationActionRequirement("application.delete"),
    "upload_candidate_document": ApplicationActionRequirement("candidate_document.upload"),
    # 先由申请动作统一校验可见性和上传职责；文件服务再校验“材料管理职责”
    # 或“本人上传”这一条具体文件的所有者限制，不能只凭接口动作绕过它。
    "rename_candidate_document": ApplicationActionRequirement("candidate_document.upload"),
    "delete_candidate_document": ApplicationActionRequirement("candidate_document.upload"),
}


# 同名状态机动作可出现在多个审核阶段，必须先结合当前状态解析为对应的
# 接口授权动作。这里改变的是动作到现有权限的映射，不会新增职责包或原子权限。
STAGE_ACTION_AUTHORIZATION_ACTIONS: dict[tuple[str, str], str] = {
    **{
        ("department_review", action): "department_decision"
        for action in ("reject", "hold", "manual_review")
    },
    **{
        ("hr_second_review", action): "hr_decision"
        for action in ("reject", "hold", "manual_review")
    },
    **{
        ("final_review", action): "final_decision"
        for action in ("offer", "reject", "manual_review")
    },
}


def application_action_requirement(
    action: str,
    *,
    application_status: str | None = None,
) -> ApplicationActionRequirement | None:
    """按“当前阶段 + 业务动作”解析接口授权要求。

    专用动作直接命中目录；reject/hold/manual_review 等跨阶段业务动作必须先
    映射到阶段接口，防止固定绑定到某一个阶段的权限。
    """

    authorization_action = STAGE_ACTION_AUTHORIZATION_ACTIONS.get(
        (str(application_status or ""), action),
        action,
    )
    return APPLICATION_ACTION_REQUIREMENTS.get(authorization_action)


RESUME_SUBMISSION_ACTION_REQUIREMENTS: dict[str, ResumeSubmissionActionRequirement] = {
    # 重新解析/发布只重放当前简历版本的自动链路；简历记录可见即可执行。
    # 即使候选人另有跨部门申请，也不能额外收紧纯恢复动作。
    "force_fresh_parse": ResumeSubmissionActionRequirement("recovery"),
    "retry_publish_resume": ResumeSubmissionActionRequirement("recovery"),
    # 当前岗位匹配会重新读取开放岗位集合，尚不是可重复使用固定输入的纯重试。
    "retry_routing": ResumeSubmissionActionRequirement(
        "sensitive_permission", "resume_submission.manage"
    ),
    "upload_replacement_resume": ResumeSubmissionActionRequirement(
        "sensitive_permission", "resume_submission.manage"
    ),
    "correct_parsed_resume": ResumeSubmissionActionRequirement(
        "sensitive_permission", "resume_submission.manage"
    ),
    "resolve_duplicate_candidates": ResumeSubmissionActionRequirement(
        "sensitive_permission", "resume_submission.manage"
    ),
    "replace_duplicate_resume": ResumeSubmissionActionRequirement(
        "sensitive_permission", "resume_submission.manage"
    ),
    "discard_submission": ResumeSubmissionActionRequirement(
        "sensitive_permission", "resume_submission.manage"
    ),
    "create_applications": ResumeSubmissionActionRequirement(
        "sensitive_permission", "application.create"
    ),
    # 已废弃的确认接口仍需保护；它会返回 409，但不得成为未授权探测入口。
    "confirm_structure": ResumeSubmissionActionRequirement("material_view"),
}

# 兼容旧调用点；纯失败恢复显式映射为 ``None``，表示没有职责权限。
# 新代码应优先消费 APPLICATION_ACTION_REQUIREMENTS 以区分“动作未登记”和
# “已登记但只需可见性”这两种不同情况。
ACTION_PERMISSIONS: dict[str, str | None] = {
    action: requirement.permission_code
    for action, requirement in APPLICATION_ACTION_REQUIREMENTS.items()
}
FIRST_INTERVIEW_ACTIONS = {
    action for action, requirement in APPLICATION_ACTION_REQUIREMENTS.items()
    if requirement.assignment == "first_interviewer"
}
SECOND_INTERVIEW_ACTIONS = {
    action for action, requirement in APPLICATION_ACTION_REQUIREMENTS.items()
    if requirement.assignment == "hr"
}
