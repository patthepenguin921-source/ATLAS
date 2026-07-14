import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        atlas: {
          // "Cool slate" — neutral dark slate, soft indigo-blue accent.
          bg: "#16181d",
          panel: "#1d2027",
          panel2: "#252932",
          border: "#333846",
          text: "#e7e9ee",
          muted: "#9096a3",
          accent: "#6a8bff",   // soft indigo-blue
          accent2: "#7dd3fc",  // sky
          good: "#4ade80",
          warn: "#fbbf24",
          bad: "#f87171",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        soft: "0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -12px rgba(0,0,0,0.5)",
        glow: "0 0 0 1px rgba(106,139,255,0.30), 0 8px 30px -10px rgba(106,139,255,0.20)",
      },
      keyframes: {
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
      },
    },
  },
  plugins: [],
};
export default config;
