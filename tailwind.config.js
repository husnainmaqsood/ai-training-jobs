/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html", "./**/templates/**/*.html", "./**/forms.py", "./**/staff_forms.py", "./**/builder_views.py", "./core/templatetags/*.py", "./static/js/app.js"],
  safelist: [{ pattern: /^avatar-c[0-7]$/ }],
  theme: {
    extend: {
      colors: {
        // Orexia blue, sampled from the logo's ring and "AI".
        brand: {
          50: "#eef4fc", 100: "#dce8f8", 200: "#bfd4f1", 300: "#91b8e5", 400: "#5f95d5", 500: "#3d76c4",
          600: "#2d5ea9", 700: "#244b8b", 800: "#1e3d70", 900: "#18315a", 950: "#0e1c30",
        },
        // The warm highlight on the ring. Accent only.
        gold: { 100: "#fcf1dc", 300: "#f6d9a8", 400: "#f0c98a", 500: "#e3a957" },
        // Navy-tinted greys; 950 is the wordmark's navy.
        ink: {
          50: "#f8fafc", 100: "#f1f4f8", 200: "#e3e8ef", 300: "#cbd3de", 400: "#98a3b3", 500: "#677489",
          600: "#4b5669", 700: "#333e52", 800: "#1f2a3d", 900: "#142033", 950: "#0e1c30",
        },
        canvas: "#fbfcfd",
        mist: "#f3f6f9",
        line: "#e6eaf0",
        "line-strong": "#d4dbe4",
      },
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "Liberation Mono", "monospace"],
      },
      fontSize: {
        "2xs": ["11px", "16px"],
      },
      borderRadius: {
        "4xl": "2rem",
      },
      // Layered, navy-tinted shadows: a tight contact shadow plus a soft ambient one.
      boxShadow: {
        xs: "0 1px 2px rgb(14 28 48 / 0.06)",
        "soft-sm": "0 2px 5px rgb(14 28 48 / 0.08), 0 1px 2px rgb(0 0 0 / 0.05)",
        "soft-md": "0 6px 12px rgb(14 28 48 / 0.08), 0 3px 6px rgb(0 0 0 / 0.05)",
        "soft-lg": "0 15px 35px rgb(14 28 48 / 0.10), 0 5px 15px rgb(0 0 0 / 0.06)",
        "soft-xl": "0 20px 60px rgb(14 28 48 / 0.15), 0 10px 20px rgb(0 0 0 / 0.07)",
        glow: "0 0 40px rgb(61 118 196 / 0.28)",
      },
      transitionTimingFunction: {
        smooth: "cubic-bezier(.4, 0, .2, 1)",
        "out-expo": "cubic-bezier(.16, 1, .3, 1)",
        spring: "cubic-bezier(.34, 1.56, .64, 1)",
      },
      keyframes: {
        "fade-up": { "0%": { opacity: "0", transform: "translateY(16px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "panel-in": { "0%": { opacity: "0", transform: "translateY(-8px) scale(.97)" }, "100%": { opacity: "1", transform: "translateY(0) scale(1)" } },
        "toast-in": { "0%": { opacity: "0", transform: "translateY(16px) scale(.96)" }, "100%": { opacity: "1", transform: "translateY(0) scale(1)" } },
        aurora: {
          "0%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(40px, -20px) scale(1.08)" },
          "100%": { transform: "translate(-20px, 25px) scale(.96)" },
        },
        float: { "0%, 100%": { transform: "translateY(0)" }, "50%": { transform: "translateY(-10px)" } },
        marquee: { "0%": { transform: "translateX(0)" }, "100%": { transform: "translateX(-50%)" } },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgb(16 185 129 / 0.55)" },
          "70%": { boxShadow: "0 0 0 7px rgb(16 185 129 / 0)" },
          "100%": { boxShadow: "0 0 0 0 rgb(16 185 129 / 0)" },
        },
      },
      animation: {
        "fade-up": "fade-up .6s cubic-bezier(.16, 1, .3, 1) both",
        "panel-in": "panel-in .22s cubic-bezier(.16, 1, .3, 1) both",
        "toast-in": "toast-in .35s cubic-bezier(.34, 1.56, .64, 1) both",
        aurora: "aurora 18s ease-in-out infinite alternate",
        "aurora-slow": "aurora 26s ease-in-out infinite alternate-reverse",
        float: "float 6s ease-in-out infinite",
        marquee: "marquee 40s linear infinite",
        "pulse-ring": "pulse-ring 2s cubic-bezier(.4, 0, .2, 1) infinite",
      },
    },
  },
  plugins: [require("@tailwindcss/forms"), require("@tailwindcss/typography")],
};
