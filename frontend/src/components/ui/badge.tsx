import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap", {
  variants: {
    variant: {
      default: "border-transparent bg-accent text-accent-foreground",
      secondary: "border-transparent bg-secondary text-secondary-foreground",
      success: "border-green-200 bg-green-50 text-green-800",
      warning: "border-amber-200 bg-amber-50 text-amber-800",
      critical: "border-red-200 bg-red-50 text-red-800",
      info: "border-blue-200 bg-blue-50 text-blue-800",
      outline: "text-foreground",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}
export const Badge = ({ className, variant, ...p }: BadgeProps) => <span className={cn(badgeVariants({ variant }), className)} {...p} />;
