import { Suspense } from "react";
import { AuditView } from "./audit-view";

export default function AuditPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-sm text-muted">載入中…</div>}>
      <AuditView />
    </Suspense>
  );
}
