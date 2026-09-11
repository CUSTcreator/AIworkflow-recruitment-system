import { Navigate, useParams } from "react-router-dom";

export function CanonicalApplicationRoute({ target }: { target: string }) {
  const { applicationId } = useParams();
  if (!applicationId) return <Navigate to="/candidates" replace />;
  return <Navigate to={`/applications/${applicationId}/${target}`} replace />;
}
