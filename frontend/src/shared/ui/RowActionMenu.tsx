import { Ellipsis } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type RowActionMenuItem = {
  /** 菜单文字应说明动作本身，例如“查看详情”“重新尝试”。 */
  label: string;
  /** 选择菜单项后的业务动作；菜单会先关闭，再执行该动作。 */
  onSelect: () => void;
  icon?: ReactNode;
  disabled?: boolean;
  destructive?: boolean;
};

/**
 * 表格行的统一操作入口。
 *
 * 所有行级“查看、编辑、重试、删除、进入工作台”等动作放在此菜单内，避免
 * 不同列表同时堆放多颗按钮。菜单用 fixed 定位渲染到 document.body，因而不会
 * 被表格的横向滚动容器裁切。
 */
export function RowActionMenu({ ariaLabel, items }: { ariaLabel: string; items: RowActionMenuItem[] }) {
  const [position, setPosition] = useState<{ top: number; left: number; placement: "above" | "below"; maxHeight: number }>();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const visibleItems = items.filter((item) => Boolean(item.label));

  useEffect(() => {
    if (!position) return;
    const closeWhenOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) setPosition(undefined);
    };
    const closeWhenEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPosition(undefined);
        triggerRef.current?.focus();
      }
    };
    const closeWhenViewportChanges = () => setPosition(undefined);
    document.addEventListener("mousedown", closeWhenOutside);
    document.addEventListener("keydown", closeWhenEscape);
    window.addEventListener("resize", closeWhenViewportChanges);
    window.addEventListener("scroll", closeWhenViewportChanges, true);
    return () => {
      document.removeEventListener("mousedown", closeWhenOutside);
      document.removeEventListener("keydown", closeWhenEscape);
      window.removeEventListener("resize", closeWhenViewportChanges);
      window.removeEventListener("scroll", closeWhenViewportChanges, true);
    };
  }, [position]);

  if (visibleItems.length === 0) return <span className="text-xs text-muted">—</span>;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition ${position ? "bg-slate-100 text-slate-800" : "text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={Boolean(position)}
        title="更多操作"
        onClick={() => {
          if (position) {
            setPosition(undefined);
            return;
          }
          const rect = triggerRef.current?.getBoundingClientRect();
          if (!rect) return;
          const menuWidth = 184;
          // 菜单优先贴在按钮下方；底部空间不足时向上展开。最大高度受视口限制，
          // 即使操作项很多，也会在菜单内部滚动而不会落到屏幕外。
          const menuHeight = Math.max(48, visibleItems.length * 44 + 12);
          const availableBelow = window.innerHeight - rect.bottom - 12;
          const availableAbove = rect.top - 12;
          const placement = availableBelow >= menuHeight || availableBelow >= availableAbove ? "below" : "above";
          // 向上展开时用 translateY(-100%) 锚定在按钮上沿；因此使用真实渲染高度，
          // 而不是估算高度来计算坐标，菜单始终紧贴按钮。
          setPosition({
            top: placement === "below" ? rect.bottom + 4 : rect.top - 4,
            left: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
            placement,
            maxHeight: Math.max(48, placement === "below" ? availableBelow : availableAbove),
          });
        }}
      >
        <Ellipsis size={17} aria-hidden="true" />
      </button>
      {position ? createPortal(
        <div ref={menuRef} role="menu" aria-label={ariaLabel} className="fixed z-50 max-h-[calc(100vh-16px)] w-48 overflow-y-auto rounded-lg border border-slate-200 bg-white p-1.5 shadow-lg shadow-slate-900/10" style={{ top: position.top, left: position.left, maxHeight: position.maxHeight, transform: position.placement === "above" ? "translateY(-100%)" : undefined }}>
          {visibleItems.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-blue-500/40 disabled:cursor-not-allowed disabled:opacity-45 ${item.destructive ? "text-rose-600 hover:bg-rose-50" : "text-slate-700 hover:bg-slate-50 hover:text-slate-900"}`}
              onClick={() => {
                setPosition(undefined);
                item.onSelect();
              }}
            >
              {item.icon ? <span className="shrink-0">{item.icon}</span> : null}
              {item.label}
            </button>
          ))}
        </div>,
        document.body
      ) : null}
    </>
  );
}
