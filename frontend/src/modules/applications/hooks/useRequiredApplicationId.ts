import { useParams } from 'react-router-dom';

export function useRequiredApplicationId(): string {
  const { applicationId } = useParams<"applicationId">();
  if (!applicationId) {
    throw new Error("当前页面缺少候选人申请信息，请返回候选人列表重新进入");
  }
  return applicationId;
}
