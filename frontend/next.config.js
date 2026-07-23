/** @type {import('next').NextConfig} */

// Surface which PR/commit is actually live, for a small "PR #NN" badge in
// the sidebar — otherwise there's no way to tell which deploy is up short
// of checking Vercel directly. Vercel sets these build-time env vars
// automatically (no project config needed): VERCEL_GIT_PULL_REQUEST_ID is
// only present on a preview build triggered directly by a PR; a production
// build (after merge, on main) has no PR id of its own, but its commit
// message is GitHub's "Merge pull request #NN from ..." — parsed as the
// fallback so the badge still shows the right number once merged.
function resolvePrNumber() {
  if (process.env.VERCEL_GIT_PULL_REQUEST_ID) return process.env.VERCEL_GIT_PULL_REQUEST_ID;
  const match = (process.env.VERCEL_GIT_COMMIT_MESSAGE || "").match(/Merge pull request #(\d+)/);
  return match ? match[1] : "";
}

const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_PR_NUMBER: resolvePrNumber(),
    NEXT_PUBLIC_COMMIT_SHA: (process.env.VERCEL_GIT_COMMIT_SHA || "").slice(0, 7),
  },
};

module.exports = nextConfig;
