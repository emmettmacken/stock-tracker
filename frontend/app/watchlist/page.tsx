import { Watchlist } from "@/components/Watchlist";

export default function WatchlistPage() {
  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-5xl mx-auto px-4 py-10 sm:py-12">
        <header className="mb-9">
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight text-white text-balance">
            Stock Signal Tracker
          </h1>
          <p className="mt-1.5 text-zinc-400 text-sm leading-relaxed">
            Six-factor composite with an HMM regime model, over ~2 years of price history
          </p>
        </header>
        <Watchlist />
      </div>
    </main>
  );
}
