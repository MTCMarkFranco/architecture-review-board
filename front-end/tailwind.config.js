/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        'ms-blue': '#0078D4',
        'ms-dark': '#1B1A19',
        'ms-gray': '#F3F2F1',
        'ms-gray-dark': '#EDEBE9',
        'ms-text': '#323130',
        'ms-text-secondary': '#605E5C',
        'ms-accent': '#0063B1',
        'ms-success': '#107C10',
        'ms-warning': '#FFB900',
        'ms-danger': '#D13438',
        'ms-purple': '#5C2D91',
        'ms-teal': '#008272',
        'ms-header': '#F9F9F9',
        'ms-row': '#FFFFFF',
        'ms-row-alt': '#FAFAFA',
        'ms-border': '#E1DFDD',
      },
      fontFamily: {
        main: ['"Segoe UI"', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
}