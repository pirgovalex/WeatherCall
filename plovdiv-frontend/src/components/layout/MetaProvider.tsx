import { createQuery } from '@tanstack/solid-query'
import type { JSX } from 'solid-js'
import type { ApiError } from '../../api/client'
import type { MetaResponse } from '../../api/types'
import { metaQueryOptions } from '../../queries/hooks'
import { MetaContext } from '../../queries/metaContext'

/** Loads /api/v1/meta once; every data query is keyed on the run id it returns. */
export default function MetaProvider(props: { children: JSX.Element }) {
  const query = createQuery<MetaResponse, ApiError>(() => metaQueryOptions())
  return <MetaContext.Provider value={{ query, runId: () => query.data?.run_id }}>{props.children}</MetaContext.Provider>
}
