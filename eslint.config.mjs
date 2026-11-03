import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["external/**", "node_modules/**", "**/dist/**", "**/test-results/**", "**/playwright-report/**"] },
  js.configs.recommended,
  {
    files: ["apps/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { document: "readonly", window: "readonly", fetch: "readonly", console: "readonly" }
    }
  },
  ...tseslint.configs.recommended.map((config) => ({ ...config, files: ["apps/**/*.{ts,vue}"] })),
  ...pluginVue.configs["flat/recommended"].map((config) => ({ ...config, files: ["apps/**/*.vue"] })),
  {
    files: ["apps/**/*.vue"],
    languageOptions: { parserOptions: { parser: tseslint.parser } }
  },
  {
    files: ["apps/**/*.{ts,vue}"],
    languageOptions: {
      globals: {
        document: "readonly",
        window: "readonly",
        fetch: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        AbortController: "readonly",
        AbortSignal: "readonly",
        DOMException: "readonly",
        Response: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        HTMLElement: "readonly",
        HTMLFormElement: "readonly",
        MediaQueryListEvent: "readonly",
        Intl: "readonly",
        process: "readonly"
      }
    },
    rules: {
      // A leading underscore marks an intentionally unused parameter (e.g. typed mock signatures).
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // Formatting is owned by Prettier.
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/multiline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
      "vue/html-indent": "off",
      "vue/html-closing-bracket-newline": "off"
    }
  }
];
