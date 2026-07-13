import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        atlas: {
          bg: "#0b0f19",
          panel: "#131826",
          panel2: "#1a2032",
          border: "#242c42",
          text: "#e6e9f2",
          muted: "#8b93ad",
          accent: "#6366f1",
          accent2: "#22d3ee",
          good: "#34d399",
          warn: "#fbbf24",
          bad: "#f87171",
        },
      },
    },
  },
  plugins: [],
};
export default config;
