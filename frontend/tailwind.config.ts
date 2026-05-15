import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./layouts/**/*.{js,ts,jsx,tsx,mdx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#141414",
          800: "#292929",
          600: "#55524d"
        },
        panel: "#fbfaf7",
        line: "#dfd8ce",
        moss: "#4e6d58",
        clay: "#a85d3b",
        steel: "#42677a"
      },
      boxShadow: {
        "table-row": "inset 0 -1px 0 rgba(20,20,20,0.08)"
      }
    }
  },
  plugins: []
};

export default config;
