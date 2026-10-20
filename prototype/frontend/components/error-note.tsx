export function ErrorNote({ e }: { e: Error }) {
  return (
    <div className="m-2 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
      無法取得資料：{e.message}（後端未啟動時可設定 NEXT_PUBLIC_MOCK=1 使用模擬資料）
    </div>
  );
}
