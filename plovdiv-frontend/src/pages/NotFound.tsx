import { A } from '@solidjs/router'
import PageHeader from '../components/ui/PageHeader'

export default function NotFound() {
  return (
    <>
      <PageHeader title="Page not found">There is no page at this address.</PageHeader>
      <A href="/" class="text-primary underline">
        Go to Now
      </A>
    </>
  )
}
