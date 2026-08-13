import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        atlas: {
          // Practically-black base, clean blue accent. Panels/border stay a
          // couple steps lighter than bg so cards and inputs still read as
          // distinct surfaces against the near-pure-black page.
          bg: "#020203",
          panel: "#0c0d10",
          panel2: "#141519",
          border: "#26272f",
          text: "#eceef3",
          muted: "#8b90a0",
          accent: "#3b82f6",   // clean blue (was a more indigo/purple-leaning #6a8bff)
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
        glow: "0 0 0 1px rgba(59,130,246,0.30), 0 8px 30px -10px rgba(59,130,246,0.20)",
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
