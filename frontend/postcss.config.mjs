/**
 * PostCSS configuration.
 *
 * Tailwind v4 ships as a PostCSS plugin in its own package
 * (`@tailwindcss/postcss`). In v3 you listed `tailwindcss` here plus
 * `autoprefixer`; in v4 this single plugin covers both — adding autoprefixer
 * alongside it causes duplicated vendor prefixes.
 */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
