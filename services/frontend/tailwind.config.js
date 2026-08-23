/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        status: {
          received: "#94a3b8",
          processing: "#38bdf8",
          succeeded: "#4ade80",
          failed: "#fb923c",
          dead_lettered: "#f87171",
        },
      },
    },
  },
  plugins: [],
};
