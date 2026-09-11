import { ChevronDown } from "lucide-react";
import type {
  FirstInterviewOriginalRecord,
  InterviewRawNoteView,
  RecordedQuestionView,
  SecondInterviewOriginalRecord
} from "@/modules/interviews/secondInterviewApi";
import { Badge } from "@/shared/ui/Badge";

function RawNoteContent({ note, emptyText }: { note?: InterviewRawNoteView; emptyText: string }) {
  if (!note?.content.trim()) return <p className="text-xs text-muted">{emptyText}</p>;
  return (
    <div>
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{note.content}</p>
      {(note.authorName || note.createdAt) ? (
        <div className="mt-3 border-t border-line pt-2 text-xs text-muted">
          {[note.authorName, note.createdAt].filter(Boolean).join(" · ")}
        </div>
      ) : null}
    </div>
  );
}

function QuestionRecordsContent({ records }: { records: RecordedQuestionView[] }) {
  const usableRecords = records.filter((record) => Boolean(
    record.answerSummary.trim() || record.interviewerNote.trim()
  ));
  if (usableRecords.length === 0) return <p className="text-xs text-muted">空</p>;
  return (
    <div className="space-y-3">
      {usableRecords.map((record, index) => (
        <div key={record.questionId} className="rounded-md border border-line p-3">
          <div className="flex items-start gap-2">
            <Badge className="shrink-0 border-slate-200 bg-slate-50 text-slate-700">Q{index + 1}</Badge>
            <div className="text-sm font-medium leading-5 text-ink">{record.questionText || "未填写题目"}</div>
          </div>
          {record.answerSummary ? <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{record.answerSummary}</p> : null}
          {record.interviewerNote ? <div className="mt-3 text-xs text-muted">备注：{record.interviewerNote}</div> : null}
        </div>
      ))}
    </div>
  );
}

export function FirstInterviewRawNotesCard({ record }: { record?: FirstInterviewOriginalRecord }) {
  return (
    <section className="rounded-md border border-line p-4">
      <h2 className="mb-3 text-sm font-semibold text-ink">一面自由记录原文</h2>
      <RawNoteContent note={record?.rawNotes} emptyText="一面面试官没有填写自由记录。" />
    </section>
  );
}

export function FirstInterviewQuestionRecordsCard({ record }: { record?: FirstInterviewOriginalRecord }) {
  const records = (record?.recordedQuestions ?? []).filter((item) => Boolean(
    item.answerSummary.trim() || item.interviewerNote.trim()
  ));
  return (
    <details className="group rounded-md border border-line">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">一面有效题目记录（{records.length}）</h2>
        <ChevronDown size={16} className="text-muted transition group-open:rotate-180" />
      </summary>
      <div className="border-t border-line p-4"><QuestionRecordsContent records={records} /></div>
    </details>
  );
}

export function InterviewOriginalRecordsSection({
  first,
  second
}: {
  first?: FirstInterviewOriginalRecord;
  second?: SecondInterviewOriginalRecord;
}) {
  return (
    <details className="group rounded-md border border-line bg-white" open>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">面试原始记录</h2>
        <ChevronDown size={16} className="text-muted transition group-open:rotate-180" />
      </summary>
      <div className="space-y-4 border-t border-line p-4">
        <section>
          <h3 className="mb-2 text-xs font-semibold text-slate-600">一面自由记录</h3>
          <RawNoteContent note={first?.rawNotes} emptyText="一面没有自由记录。" />
        </section>
        <section className="border-t border-line pt-4">
          <h3 className="mb-2 text-xs font-semibold text-slate-600">一面有效题目记录</h3>
          <QuestionRecordsContent records={first?.recordedQuestions ?? []} />
        </section>
        <section className="border-t border-line pt-4">
          <h3 className="mb-2 text-xs font-semibold text-slate-600">二面自由记录</h3>
          <RawNoteContent note={second?.rawNotes} emptyText="二面没有自由记录。" />
        </section>
      </div>
    </details>
  );
}
