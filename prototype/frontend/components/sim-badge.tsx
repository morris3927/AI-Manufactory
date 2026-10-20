import { cn } from "@/lib/utils";

/** 競賽規定：資料畫面都要標示「模擬資料」 */
export function SimBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex select-none items-center rounded-sm border border-fuchsia-400/50 bg-fuchsia-500/10 px-1 py-px text-[10px] font-medium leading-none tracking-wider text-fuchsia-300",
        className,
      )}
      title="畫面中的工廠、人員、影像與數據皆為模擬資料"
    >
      模擬資料
    </span>
  );
}
