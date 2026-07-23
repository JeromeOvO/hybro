import nextConfig from "eslint-config-next";

export default [
  ...nextConfig,
  {
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
    rules: {
      "react/no-unescaped-entities": "off",
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/rules-of-hooks": "warn",
      "@next/next/no-img-element": "off",
      "jsx-a11y/alt-text": "warn",
      "testing-library/no-unnecessary-act": "off",
    },
  },
];
