"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

export function Nav() {
  const path = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <nav className="border-b border-zinc-800 bg-zinc-950">
      <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-6">
        <span className="text-sm font-semibold text-zinc-300">Stock Tracker</span>
        <div className="flex gap-5 flex-1">
          {([
            ["/", "Watchlist"],
            ["/portfolio", "Portfolio"],
            ["/automation", "Automation"],
          ] as const).map(([href, label]) => (
            <Link
              key={href}
              href={href}
              className={`text-sm transition-colors ${
                path === href ? "text-white font-medium" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {label}
            </Link>
          ))}
        </div>
        <button
          onClick={handleLogout}
          className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}
