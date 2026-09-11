"""Interview guides 对外稳定入口：其他模块只能通过此文件访问题纲模板公开能力。"""

from backend.app.modules.interview_guides.service import InterviewGuideTemplateService

__all__ = ["InterviewGuideTemplateService"]

