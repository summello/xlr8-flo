# Workflow list and route preview

Open `/admin/approval-workflows` to search and page through definitions. Versions opens
read-only lineage and compares the current draft with the published version. In Flight
counts running documents pinned to older versions; publishing never moves those documents.

In the builder, Route Preview evaluates the saved draft version with an estimate and the
selected BU/OU. Route Preview saves and checks local graph edits before evaluating the
saved version. The preview writes
no instance, task or decision. Approvers are resolved live; there is no requester in this
administrative sample, so requester separation of duty is applied only on real submission.
Other eligibility exclusions still apply. Invalid estimates preserve the typed entry and
show an error; incomplete drafts must be corrected before their route can be evaluated.

The list and sheets require `workflow.read` at the workflow scope; creation requires
`workflow.manage`. Foreign-tenant workflow and BU/OU identifiers return 404.
