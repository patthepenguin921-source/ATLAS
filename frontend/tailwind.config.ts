import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        atlas: {
          // "Ink + electric lime" — near-black desaturated ink, lime accent,
          // cool teal-grey secondary.
          bg: "#0c0e0d",
          panel: "#141715",
          panel2: "#1b1f1c",
          border: "#2a302b",
          text: "#e8ebe6",
          muted: "#8b948a",
          accent: "#b6f36b",   // electric lime
          accent2: "#7dd3c0",  // cool teal-grey
          good: "#86e39a",
          warn: "#e9c46a",
          bad: "#f4776b",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        soft: "0 1px 2px rgba(0,0,0,0.3), 0 8px 24px -12px rgba(0,0,0,0.5)",
        glow: "0 0 0 1px rgba(182,243,107,0.25), 0 8px 30px -10px rgba(182,243,107,0.15)",
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
