/**
 * Next.js configuration.
 *
 * Deliberately minimal. Next's defaults are good, and every option added here
 * is one more thing that can break on upgrade.
 */
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Surfaces problems that would otherwise only appear in production builds:
  // in development, components render twice to expose side effects that are
  // not idempotent. It does NOT affect the production build.
  reactStrictMode: true,

  // Do not leak the framework version in response headers.
  poweredByHeader: false,

  // NOTE: `typescript.ignoreBuildErrors` and `eslint.ignoreDuringBuilds` are
  // intentionally absent. A build that fails on a type error is the point —
  // switching them on to ship faster is how type safety quietly dies.
};

export default nextConfig;
