/**
 * Next.js configuration.
 *
 * Deliberately minimal. Next's defaults are good, and every option added here
 * is one more thing that can break on upgrade.
 */
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a plain folder of HTML/CSS/JS instead of running a Node server.
  //
  // This app can do that because every page is client-side: all six routes
  // fetch from the API in the browser, and none of them render on the server.
  // The build already reported every route as static before this was set, so
  // the option changes the OUTPUT, not the behaviour.
  //
  // It is what lets Netlify host the frontend on a CDN for free, with no
  // Next.js runtime adapter in the way.
  output: "export",

  // Static export has no server to run Next's image optimiser, so it must be
  // switched off. Nothing here uses next/image — the only <img> is the scan
  // preview, which is a local object URL — so this costs nothing.
  images: { unoptimized: true },

  // Emit `practice/index.html` rather than `practice.html`, so a static host
  // serves /practice and /practice/ identically. Without it one of the two
  // 404s, and which one depends on the host.
  trailingSlash: true,

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
