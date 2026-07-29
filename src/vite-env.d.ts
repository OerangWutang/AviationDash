/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the Atlas Argus backend (e.g. http://localhost:8100).
   *  Unset = local mode: frontend state + localStorage working copy. */
  readonly VITE_API_URL?: string;
  /** Show the sample-case demo logins on the sign-in screen (demo builds). */
  readonly VITE_DEMO_LOGINS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
