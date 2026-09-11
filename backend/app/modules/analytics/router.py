"""分析看板的 HTTP 适配层：只校验权限并返回只读统计结果。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.analytics.query_service import AnalyticsQueryService
from backend.app.modules.analytics.schemas import AnalyticsReadModel
from backend.app.modules.auth.public import AuthorizationService
from backend.app.modules.auth.public import get_current_user


router = APIRouter(tags=["analytics"])


@router.get("/analytics/overview", response_model=AnalyticsReadModel)
def analytics_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyticsReadModel:
    # 统计是只读数据展示，沿用候选人可见性和数据范围，不单独配置业务权限。
    AuthorizationService(db).require_data_view(user)
    return AnalyticsQueryService(db).overview(user)
