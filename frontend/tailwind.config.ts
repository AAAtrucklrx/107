import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", "[data-theme='dark']"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Noto Sans SC Variable", "sans-serif"],
        display: ["Noto Serif SC Variable", "serif"],
      },
      colors: {
        ink: "var(--ink)",
        muted: "var(--muted)",
        line: "var(--line)",
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        primary: "var(--primary)",
        retrieval: "var(--retrieval)",
      },
    },
  },
  plugins: [],
} satisfies Config;
