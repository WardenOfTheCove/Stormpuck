"use client";

import * as React from "react";

export function Badge({
  className = "",
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  variant?: "default" | "secondary" | "outline" | "success" | "warning";
}) {
  const variants = {
    default: "border-transparent bg-zinc-100 text-zinc-900",
    secondary: "border-transparent bg-zinc-800 text-zinc-300",
    outline: "border-zinc-800 text-zinc-400",
    success: "border-emerald-500/20 bg-emerald-500/10 text-emerald-400",
    warning: "border-amber-500/20 bg-amber-500/10 text-amber-400",
  };

  return (
    <div
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors ${variants[variant]} ${className}`}
      {...props}
    />
  );
}