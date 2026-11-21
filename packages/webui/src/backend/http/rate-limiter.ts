export interface RateLimiterOptions {
  /** Maximum requests per second sustained. Defaults to 1. */
  rps?: number;
  /** Stable server-side name for a shared SQL quota. */
  scope?: string;
  /** Maximum burst capacity allowed. Defaults to 3. */
  burst?: number;
  /** Custom clock function for unit testing. */
  now?: () => number;
}

export interface RateLimitResult {
  allowed: boolean;
  retryAfter: number;
  remaining: number;
}

export interface RateLimiter {
  consume: (key: string, cost?: number) => RateLimitResult;
  reset?: () => void;
  consumeAsync?: (key:string,cost?:number)=>Promise<RateLimitResult>;
}

/**
 * In-memory Token Bucket rate limiter with automatic stale-entry cleanup.
 */
export function createRateLimiter(options: RateLimiterOptions = {}): RateLimiter {
  const rps = options.rps ?? 1;
  const capacity = options.burst ?? 3;
  if (!Number.isFinite(rps) || rps <= 0 || rps > 10000 || !Number.isSafeInteger(capacity) || capacity < 1 || capacity > 10000)
    throw new Error('Invalid rate limit configuration');
  const now = options.now ?? (() => Date.now());
  const refillRatePerMs = rps / 1000;

  interface Bucket {
    tokens: number;
    lastRefill: number;
  }

  const buckets = new Map<string, Bucket>();

  function cleanupStaleEntries(currentTime: number) {
    if (buckets.size < 1000) return;
    const maxAgeMs = 10 * 60 * 1000; // 10 minutes
    for (const [key, bucket] of buckets.entries()) {
      if (currentTime - bucket.lastRefill > maxAgeMs) {
        buckets.delete(key);
      }
    }
  }

  const limiter:RateLimiter = {
    consume(key: string, cost = 1): RateLimitResult {
      if (!Number.isFinite(cost) || cost <= 0 || cost > capacity) throw new Error('Invalid rate limit cost');
      const currentTime = now();
      cleanupStaleEntries(currentTime);

      let bucket = buckets.get(key);
      if (!bucket) {
        // Refuse new identities at capacity rather than evicting active limits.
        if (buckets.size >= 50000) return {allowed:false,retryAfter:600,remaining:0};
        bucket = { tokens: capacity, lastRefill: currentTime };
        buckets.set(key, bucket);
      } else {
        const elapsed = Math.max(0, currentTime - bucket.lastRefill);
        bucket.tokens = Math.min(capacity, bucket.tokens + elapsed * refillRatePerMs);
        bucket.lastRefill = currentTime;
      }

      if (bucket.tokens >= cost) {
        bucket.tokens -= cost;
        return {
          allowed: true,
          retryAfter: 0,
          remaining: Math.floor(bucket.tokens),
        };
      }

      const needed = cost - bucket.tokens;
      const retryAfterSeconds = Math.max(1, Math.ceil(needed / (refillRatePerMs * 1000)));

      return {
        allowed: false,
        retryAfter: retryAfterSeconds,
        remaining: 0,
      };
    },
    async consumeAsync(key:string,cost=1) {
      if(!Number.isFinite(cost) || cost<=0 || cost>capacity)throw new Error('Invalid rate limit cost');
      if(options.scope) {
        const {getDatabasePool}=await import('../chat/db');
        const pool=getDatabasePool();
        if(pool) {
          const {consumeDatabaseQuota}=await import('./database-quota');
          return consumeDatabaseQuota(pool,options.scope,key,rps,capacity,cost);
        }
        if(['prod','production'].includes(process.env.KB_ENV??''))throw new Error('Shared quota database unavailable');
      }
      return limiter.consume(key,cost);
    },
    reset() {
      buckets.clear();
    },
  };
  return limiter;
}
