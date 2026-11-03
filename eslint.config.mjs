import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import vueParser from "vue-eslint-parser";

const browserGlobals = {
  document: "readonly",
  window: "readonly",
  fetch: "readonly",
  console: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  HTMLElement: "readonly",
  HTMLFormElement: "readonly",
  MediaQueryListEvent: "readonly",
  Intl: "readonly",
};

export default [
  { ignores: ["external/**", "node_modules/**", "dist/**"] },
  js.configs.recommended,
  {
    files: ["apps/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: browserGlobals,
    },
  },
  // Vue 3 + TypeScript apps (ADR-002). Type checking itself is vue-tsc's job.
  ...tseslint.configs.recommended.map((config) => ({
    ...config,
    files: ["apps/**/*.ts", "apps/**/*.vue"],
  })),
  ...pluginVue.configs["flat/recommended"].map((config) => ({
    ...config,
    files: ["apps/**/*.vue"],
  })),
  {
    files: ["apps/**/*.ts", "apps/**/*.vue"],
    languageOptions: {
      parser: vueParser,
      parserOptions: { parser: tseslint.parser, sourceType: "module", extraFileExtensions: [".vue"] },
      globals: browserGlobals,
    },
    rules: {
      // Prettier owns layout; keep ESLint to correctness and Vue semantics.
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/multiline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
      "vue/html-indent": "off",
      "vue/html-closing-bracket-newline": "off",
    },
  },
];
