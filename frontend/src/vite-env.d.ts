/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the API lives. Defaults to /api, which nginx proxies in production
   *  and the Vite dev server proxies locally. */
  readonly VITE_API_BASE?: string
  /** "1" serves every response from src/fixtures (no backend). */
  readonly VITE_FIXTURES?: string
  /** Base URL where source PDFs are published; unset = no source links. */
  readonly VITE_SOURCE_DOCS_URL?: string
  readonly VITE_TILE_URL?: string
  readonly VITE_TILE_ATTRIBUTION?: string
  readonly VITE_TILE_MAX_ZOOM?: string
  readonly VITE_TILE_SUBDOMAINS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
