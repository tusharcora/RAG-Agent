/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        // Dark surface system — replaces plain slate-* utilities used before this
        // pass. Named "ink" rather than overriding slate so any leftover slate-*
        // class is still obviously stale during review rather than silently
        // matching by coincidence.
        ink: {
          950: "#0a0a0d",
          900: "#131316",
          850: "#18181c",
          800: "#202024",
          700: "#2b2b31",
          600: "#3c3c44",
          500: "#5c5c66",
          400: "#85858f",
          300: "#a9a9b3",
          200: "#c8c8d1",
          100: "#e6e6ea",
          50: "#f6f6f8",
        },
        // Primary accent — coral/red, from the reference dashboard.
        coral: {
          50: "#fef2f2",
          100: "#fde3e3",
          200: "#fbc8c9",
          300: "#f79fa1",
          400: "#f16d70",
          500: "#e8464a", // primary
          600: "#cf3236",
          700: "#ad272a",
          800: "#8c2225",
          900: "#742224",
          950: "#3f0e10",
        },
        // Secondary accent — warm gold, from the reference dashboard.
        gold: {
          50: "#fffbeb",
          100: "#fef2c6",
          200: "#fde48a",
          300: "#fbd04e",
          400: "#f9bb28", // secondary
          500: "#f0a012",
          600: "#d17d0d",
          700: "#ad5c0f",
          800: "#8c4813",
          900: "#743c14",
        },
        status: {
          received: "#85858f",
          processing: "#f9bb28",
          succeeded: "#34d399",
          failed: "#f16d70",
          dead_lettered: "#e8464a",
        },
      },
      boxShadow: {
        panel: "0 1px 2px 0 rgb(0 0 0 / 0.4), 0 0 0 1px rgb(255 255 255 / 0.04)",
        glow: "0 0 40px -8px rgb(232 70 74 / 0.35)",
      },
    },
  },
  plugins: [],
};
