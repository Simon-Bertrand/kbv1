import assert from 'node:assert/strict';
import test from 'node:test';
import { createRateLimiter } from './rate-limiter.ts';

test('createRateLimiter allows burst up to capacity and refills at 1 req/s', () => {
  let currentTime = 1000;
  const limiter = createRateLimiter({
    rps: 1,
    burst: 2,
    now: () => currentTime,
  });

  // Request 1: Allowed (1 token remaining)
  const res1 = limiter.consume('alice');
  assert.equal(res1.allowed, true);
  assert.equal(res1.remaining, 1);

  // Request 2: Allowed (0 tokens remaining)
  const res2 = limiter.consume('alice');
  assert.equal(res2.allowed, true);
  assert.equal(res2.remaining, 0);

  // Request 3 immediate: Blocked (rate limit exceeded)
  const res3 = limiter.consume('alice');
  assert.equal(res3.allowed, false);
  assert.equal(res3.retryAfter, 1);

  // Advance time by 500ms: still not enough for 1 token
  currentTime += 500;
  const res4 = limiter.consume('alice');
  assert.equal(res4.allowed, false);
  assert.equal(res4.retryAfter, 1);

  // Advance time by another 500ms (1000ms elapsed since last refill)
  currentTime += 500;
  const res5 = limiter.consume('alice');
  assert.equal(res5.allowed, true);

  // Different user 'bob' has their own independent bucket
  const resBob = limiter.consume('bob');
  assert.equal(resBob.allowed, true);
  assert.equal(resBob.remaining, 1);
});
