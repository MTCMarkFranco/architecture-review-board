/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html","./src/**/*.{html,js}", "./src/main.tsx", "./src/App.tsx", "./**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        marigold: '#ECAB23',
        blue: '#0E3846',
        warmyellow: '#f9eab8',
        warmgrey: '#a59e8c'
      },
      fontFamily: {
        main: ["Sunlife", "sans-serif"],
      },
    },
  },
  plugins: [],
}