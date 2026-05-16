export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse bg-zinc-800 rounded ${className}`} />;
}

export function SkeletonBlock() {
  return (
    <div className="space-y-2 py-1">
      <Skeleton className="h-3.5 w-3/4" />
      <Skeleton className="h-3.5 w-1/2" />
      <Skeleton className="h-3.5 w-2/3" />
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="bg-zinc-800/50 rounded-lg p-3 space-y-2 animate-pulse">
      <Skeleton className="h-3 w-1/3" />
      <Skeleton className="h-5 w-1/2" />
      <Skeleton className="h-3 w-2/3" />
    </div>
  );
}
