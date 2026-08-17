/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // One restrained accent, used for the send action and active nav only.
      colors: {
        ink: {
          50: "#f6f7f9",
          100: "#eceef2",
          200: "#d5dae2",
          300: "#b0b9c7",
          400: "#8492a6",
          500: "#64748b",
          600: "#4d5a6d",
          700: "#3f4a59",
          800: "#36404b",
          900: "#1f252d",
        },
        accent: {
          50: "#eef4ff",
          100: "#dbe6fe",
          200: "#bfd3fe",
          500: "#3b6ef3",
          600: "#2f56d4",
          700: "#2745ab",
        },
      },
    },
  },
  plugins: [],
};
