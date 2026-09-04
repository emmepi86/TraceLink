# Findings — Zulip (external validation)

Written from the project's own merged pull requests, before TraceLink was run.

## ZUL-001 — a name ending in pipe-digits collides with mention syntax [HIGH]
### STATUS: OPEN

`check_full_name` rejects names ending in `|<digits>` because they are ambiguous with Zulip's mention syntax. The check had a bypass.

## ZUL-002 — S3 will not let tusd copy an object we are still reading [HIGH]
### STATUS: OPEN

`handle_upload_pre_finish_hook` must not hold a `GetObject` open while the pre-finish hook issues a self-`CopyObject` on the same object.

## ZUL-003 — Slack exports emoji with skintone variants [MEDIUM]
### STATUS: OPEN

`build_reactions` sees reaction names like `+1::skintone-2` in Slack exports. Everything after `::` is dropped — a workaround, not a mapping.

## ZUL-004 — django-scim2 conflates SCIM users with Django users [MEDIUM]
### STATUS: OPEN

`MessagePartial` sits in the export path that had to stop treating a SCIM client as a `UserProfile`; the conflation is upstream's, and the shim here is type-unsafe on purpose.

## ZUL-005 — guests do not get default channels the way members do [MEDIUM]
### STATUS: OPEN

`do_create_user` carries the option that decides whether an invited guest joins the default channels of an organisation that is not open to anyone.

## ZUL-006 — a background update has no acting user to attribute [MEDIUM]
### STATUS: OPEN

`billing_page` belongs to the billing paths where an audit-log write may be triggered by background code that happens to have an acting user in scope; the write must say it was a background update.

## ZUL-007 — events must be sent after the transaction commits [MEDIUM]
### STATUS: OPEN

`do_add_submessage` is one of the actions converted to send its event on commit rather than inside the transaction.

## ZUL-008 — attachment access is validated per request, not cached [MEDIUM]
### STATUS: OPEN

`validate_attachment_request` decides access for each request; removal is a separate path.

## ZUL-009 — registration is a two-stage flow [MEDIUM]
### STATUS: OPEN

`stage_two_of_registration` is reached only after the first stage has established the realm and the email.

## ZUL-010 — the homepage form cleans the email before anything else [MEDIUM]
### STATUS: OPEN

`clean_email` in the homepage form is where an address is normalised and rejected before any account exists.

## ZUL-011 — django_scim2 needs request.user present to authenticate [MEDIUM]
### STATUS: OPEN

In `zerver/middleware.py`, `process_request` sets the SCIM client on `request.user` — a type-unsafe workaround kept until the upstream dependency stops requiring it.

## ZUL-012 — the Slack importer lives in one module [MEDIUM]
### STATUS: OPEN

`zerver/data_import/slack.py` holds the import quirks for Slack exports; the emoji skintone handling is one of several.

## ZUL-013 — provisioning a fresh Codespace used to fail in several ways [MEDIUM]
### STATUS: OPEN

`tools/lib/provision_inner.py` is the inner provisioning step that had to learn about Codespaces.

## ZUL-014 — a management command entry point is not unique [MEDIUM]
### STATUS: OPEN

A note naming `Command` cannot be resolved: every management command defines one.

## ZUL-015 — documentation policy is not code [MEDIUM]
### STATUS: OPEN

The project's contributor documentation is maintained as a first-class artefact. No module represents that decision.
