import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default [
  ...nextVitals,
  ...nextTs,
  {
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
    rules: {
      "react/no-unescaped-entities": "off",
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
      "@next/next/no-img-element": "off",
      "jsx-a11y/alt-text": "warn",
      "testing-library/no-unnecessary-act": "off",
    },
  },
  // Tests commonly use `any` for mocks/fixtures; keep the rule on for app code.
  {
    files: [
      "**/*.test.ts",
      "**/*.test.tsx",
      "**/*.spec.ts",
      "**/*.spec.tsx",
      "**/__tests__/**",
      "tests/**",
    ],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
];
