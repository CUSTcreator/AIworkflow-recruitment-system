import { Eye } from "lucide-react";
import { useState } from "react";
import { useAuth } from "@/modules/auth/AuthProvider";
import { requestUrlResponse, toAbsoluteApiUrl } from "@/shared/api/httpClient";
import { toUserFacingError } from "@/shared/utils/displayText";

export function AuthenticatedPdfLink({ url }: { url: string }) {
  const { token } = useAuth();
  const [error, setError] = useState("");

  async function openPdf() {
    setError("");
    const preview = window.open("", "_blank");
    // 候选人资料接口返回的是 /api/v1 开头的相对地址；统一转到 API origin，
    // 避免浏览器把 PDF 请求错误发给前端静态站点。
    const response = await requestUrlResponse(token, toAbsoluteApiUrl(url) ?? url).catch((reason) => {
      setError(
        toUserFacingError(
          reason instanceof Error ? reason.message : "简历 PDF 暂时无法读取",
        ),
      );
      return undefined;
    });
    if (!response) {
      preview?.close();
      return;
    }
    const objectUrl = URL.createObjectURL(await response.blob());
    if (preview) preview.location.href = objectUrl;
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  }

  return (
    <div className="mt-2">
      <button
        className="inline-flex items-center gap-1 text-xs font-semibold text-blue-700 hover:text-blue-800"
        type="button"
        onClick={() => void openPdf()}
      >
        <Eye size={13} aria-hidden="true" />
        查看 PDF
      </button>
      {error ? (
        <p className="mt-1 text-xs leading-5 text-rose-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
