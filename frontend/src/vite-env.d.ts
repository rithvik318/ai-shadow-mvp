/**
 * Ambient declarations normally supplied by `vite/client`.
 *
 * Written out rather than referenced so that the pure-TypeScript modules can
 * be typechecked and tested without the bundler's types present — which is
 * what lets `npm test` run before `npm install` has fetched Vite.
 */

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env?: ImportMetaEnv;
}

declare module "*.css" {
  const content: string;
  export default content;
}

declare module "*.svg" {
  const source: string;
  export default source;
}
