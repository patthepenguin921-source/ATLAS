/** @type {import('next').NextConfig} */
const { execSync } = require("child_process");

// Surface which PR/commit is actually live, for a small "PR #NN" badge in
// the sidebar — otherwise there's no way to tell which deploy is up short
// of checking the hosting provider directly. This app deploys to more than
// one host (Vercel, and Firebase App Hosting per apphosting.yaml — see its
// comment), so this can't rely on Vercel's git env vars alone; every host
// that builds from a real git checkout has `git` available at build time,
// so that's the primary source, with Vercel's own (faster, no subprocess)
// env vars preferred when present.
function readGit(cmd) {
  try {
    return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return ""; // not a git checkout, git unavailable, or a shallow clone missing what's asked for
  }
}

function resolvePrNumber() {
  // VERCEL_GIT_PULL_REQUEST_ID is only present on a preview build triggered
  // directly by a PR; a production build (after merge, on main) has no PR
  // id of its own, but its commit message is GitHub's "Merge pull request
  // #NN from ..." — parsed as the fallback so the badge still shows the
  // right number once merged, on any host.
  if (process.env.VERCEL_GIT_PULL_REQUEST_ID) return process.env.VERCEL_GIT_PULL_REQUEST_ID;
  const commitMessage = process.env.VERCEL_GIT_COMMIT_MESSAGE || readGit("git log -1 --format=%s");
  const match = commitMessage.match(/Merge pull request #(\d+)/);
  return match ? match[1] : "";
}

function resolveCommitSha() {
  return (process.env.VERCEL_GIT_COMMIT_SHA || readGit("git rev-parse HEAD")).slice(0, 7);
}

const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_PR_NUMBER: resolvePrNumber(),
    NEXT_PUBLIC_COMMIT_SHA: resolveCommitSha(),
  },
};

module.exports = nextConfig;
