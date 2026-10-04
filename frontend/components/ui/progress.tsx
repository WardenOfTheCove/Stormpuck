"use client";

import * as React from "react";

export function Progress({ value = 0, className = "" }: { value?: number; className?: string }) {
  return (
    <div className={`relative h-2 w-full overflow-hidden rounded-full bg-zinc-800 ${className}`}>
      <div
        className="h-full bg-sky-500 transition-all duration-300 ease-out"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}