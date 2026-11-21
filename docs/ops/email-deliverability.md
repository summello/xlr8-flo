# Email deliverability runbook

Resend posts delivery events to `/api/v1/webhooks/email` (Svix-signed). A hard bounce or complaint
suppresses the address (EML-003).

- **503 from the webhook:** a PO email's event arrived before its route row (`email_message_route`,
  written by `OutboxStore.sent()` after the provider call returned). PO emails carry the Resend tag
  `kind=po`; the provider retries the event, and the next delivery finds the route. Nothing to do.
- **Sustained 503s for more than an hour:** `sent()` is failing after the email left. Events for that
  message stop being retried after `UNROUTED_RETRY_SECONDS` (3600) and are dropped, so the
  transmission stays `sent` and a bounce is not suppressed. Check the outbox row and the dispatcher
  logs; add the address to the suppression list by hand if the provider shows a hard bounce.
- Non-PO emails (invitations, approvals) have no route by design; their events answer 200 and are
  not stored.
