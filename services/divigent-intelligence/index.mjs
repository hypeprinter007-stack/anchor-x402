// Divigent intelligence sidecar — Node 22 Lambda wrapping @divigent/sdk.
//
// Why this exists as a separate Lambda: the SDK is TS/JS-only and the
// intelligence layer (assessLiquidity / ensurePaymentReady) is meant to
// stay inside Divigent's compiled artifact, not be reimplemented in our
// Python codebase. This Lambda provides a thin JSON RPC over the SDK so
// the Python execution layer can consume decisions without ever seeing
// the math.
//
// The Lambda has NO walletClient — it can only read. Execution stays in
// the Python sweep Lambda which holds the operator key in Secrets Manager.

import { createPublicClient, http } from 'viem';
import { base } from 'viem/chains';
import { Divigent } from '@divigent/sdk';

const BASE_RPC_URL = process.env.BASE_RPC_URL || 'https://mainnet.base.org';

const publicClient = createPublicClient({
  chain: base,
  transport: http(BASE_RPC_URL),
});

// One Divigent instance per cold start. SDK constructor is synchronous —
// just stores config + addresses. Reused across warm invocations.
const divigent = Divigent.create({ publicClient, chain: 'base' });

// Convert bigint values to strings for JSON serialization. The Python side
// parses them back into ints / uses them as atomic units (6 decimals).
function serialize(value) {
  if (typeof value === 'bigint') return value.toString();
  if (Array.isArray(value)) return value.map(serialize);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = serialize(v);
    return out;
  }
  return value;
}

// Parse policy values that arrive as decimal strings from the Python side.
function toBigint(v) {
  if (v == null || v === '') return undefined;
  return BigInt(v);
}

function buildPolicyContext(input = {}) {
  return {
    minOperatingBalance: toBigint(input.minOperatingBalance),
    upcomingKnownPayouts: toBigint(input.upcomingKnownPayouts),
    knownUpcomingOutflows: toBigint(input.knownUpcomingOutflows),
    maxDeployablePercent: input.maxDeployablePercent,
    riskPreference: input.riskPreference,
    recentPaymentEma: toBigint(input.recentPaymentEma),
    reserveRatio: input.reserveRatio,
    reserveMultiplier: input.reserveMultiplier,
  };
}

export async function handler(event) {
  const action = event?.action;
  if (!action) {
    return { ok: false, error: 'missing_action', supported: ['assessLiquidity'] };
  }

  if (action === 'assessLiquidity') {
    const wallet = event.wallet;
    if (!wallet) return { ok: false, error: 'missing_wallet' };

    try {
      const assessment = await divigent.assessLiquidity({
        wallet,
        pendingPaymentAmount: toBigint(event.pendingPaymentAmount),
        policyContext: buildPolicyContext(event.policyContext),
        includeVenueHealth: event.includeVenueHealth ?? true,
        minDeposit: toBigint(event.minDeposit),
        recallSlippageBps: event.recallSlippageBps,
      });
      return { ok: true, assessment: serialize(assessment) };
    } catch (err) {
      return {
        ok: false,
        error: 'assess_failed',
        name: err?.name || null,
        code: err?.code || null,
        message: String(err?.message || err),
      };
    }
  }

  return { ok: false, error: 'unknown_action', action };
}
