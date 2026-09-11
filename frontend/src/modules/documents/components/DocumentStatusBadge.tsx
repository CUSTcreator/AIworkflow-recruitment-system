import { getDocumentStatusPresentation } from "../status";
import { Badge } from "@/shared/ui/Badge";

export function DocumentStatusBadge({ status }: { status: string }) {
  const presentation = getDocumentStatusPresentation(status);
  return <Badge className={presentation.tone}>{presentation.label}</Badge>;
}
