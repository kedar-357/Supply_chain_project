import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

const styles = {
  info: ["border-blue-200 bg-blue-50 text-blue-900", Info],
  success: ["border-green-200 bg-green-50 text-green-900", CheckCircle2],
  warning: ["border-amber-200 bg-amber-50 text-amber-900", AlertTriangle],
  error: ["border-red-200 bg-red-50 text-red-900", XCircle],
} as const;

export function Alert({
  variant = "info",
  title,
  children,
  className,
}: {
  variant?: keyof typeof styles;
  title?: string;
  children?: ReactNode;
  className?: string;
}) {
  const [cls, Icon] = styles[variant];
  return (
    <div role={variant === "error" ? "alert" : "status"} className={cn("flex gap-2.5 rounded-md border px-3 py-2.5 text-sm", cls, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={cn(title && "mt-0.5", "text-[13px] opacity-90")}>{children}</div>}
      </div>
    </div>
  );
}

export const Skeleton = ({ className }: { className?: string }) => <div className={cn("animate-pulse rounded-md bg-secondary", className)} />;
