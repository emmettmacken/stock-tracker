"use client";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

// App Router doesn't reset scroll when navigating between dynamic routes,
// so force the viewport back to the top on every path change.
export function ScrollToTop() {
  const pathname = usePathname();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}
