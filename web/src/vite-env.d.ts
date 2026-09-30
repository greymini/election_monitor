/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the API lives. Defaults to /api, which nginx proxies in production
   *  and the Vite dev server proxies locally. */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
