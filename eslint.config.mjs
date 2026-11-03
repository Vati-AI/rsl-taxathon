import js from "@eslint/js";
export default [
  { ignores: ["external/**", "node_modules/**", "dist/**"] },
  js.configs.recommended,
  {
    files: ["apps/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { document: "readonly", window: "readonly", fetch: "readonly", console: "readonly" }
    }
  }
];
