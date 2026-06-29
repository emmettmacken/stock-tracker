"use client";
import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { BASE } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${BASE}/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (res.ok) {
        router.push("/");
        router.refresh();
      } else if (res.status === 401) {
        setError("Invalid credentials or unverified account.");
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Something went wrong — try again");
      }
    } catch {
      setError("Something went wrong — try again");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="flex items-center justify-center gap-2 text-2xl font-semibold tracking-tight text-white mb-1">
          <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" aria-hidden />
          Stock Tracker
        </h1>
        <p className="text-zinc-500 text-sm text-center mb-8">Sign in to your account</p>

        <form onSubmit={handleSubmit} className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 flex flex-col gap-4">
          <div>
            <label htmlFor="email" className="block text-xs font-medium text-zinc-400 mb-1.5">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder-zinc-600
                focus:outline-none focus:border-zinc-600 transition-colors duration-150 ease-out-quart"
              placeholder="you@example.com"
              autoComplete="email"
              autoFocus
              required
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-xs font-medium text-zinc-400 mb-1.5">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder-zinc-600
                focus:outline-none focus:border-zinc-600 transition-colors duration-150 ease-out-quart"
              placeholder="••••••••"
              autoComplete="current-password"
              required
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-zinc-100 hover:bg-white disabled:opacity-50 disabled:cursor-not-allowed text-zinc-900 text-sm font-medium rounded-lg px-4 py-2
              transition-[background-color,transform] duration-150 ease-out-quart active:scale-[0.98]"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-zinc-500 text-sm text-center mt-6">
          No account?{" "}
          <Link href="/register" className="text-zinc-300 hover:text-white underline underline-offset-2">
            Create one
          </Link>
        </p>
      </div>
    </main>
  );
}
