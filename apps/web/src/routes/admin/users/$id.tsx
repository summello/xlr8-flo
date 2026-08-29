import { createColumnHelper } from "@tanstack/react-table";
import { ShieldCheck, UserMinus } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "../../../components/grid/DataGrid";
import { GRID_FEATURES } from "../../../components/grid/config";
import StatusPill from "../../../components/status/StatusPill";
import Surface from "../../../components/ui/Surface";

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} is not an object.`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} is missing.`);
  }
  return value;
}

function optionalText(value: unknown, label: string): string | null {
  if (value === null) return null;
  return text(value, label);
}

function userStatus(value: unknown): "active" | "deactivated" {
  if (value !== "active" && value !== "deactivated") {
    throw new Error("User status is invalid.");
  }
  return value;
}

function parseGrant(value: unknown) {
  const source = record(value, "Grant");
  const scopeType = text(source.scope_type, "Grant scope type");
  if (!(["org", "bu", "project"] as const).includes(scopeType as "org")) {
    throw new Error("Grant scope type is invalid.");
  }
  return {
    effectiveFrom: optionalText(source.effective_from, "Grant effective date"),
    grantedAt: text(source.granted_at, "Grant date"),
    grantedBy: optionalText(source.granted_by, "Grantor"),
    id: text(source.id, "Grant id"),
    role: text(source.role, "Role code"),
    roleName: text(source.role_name, "Role name"),
    scopeId: text(source.scope_id, "Grant scope id"),
    scopeName: text(source.scope_name, "Grant scope name"),
    scopeType,
  };
}

function parseAccess(value: unknown) {
  const source = record(value, "Effective access response");
  const user = record(source.user, "User");
  if (!Array.isArray(source.grants) || !Array.isArray(source.pending_grants)) {
    throw new Error("Grant lists are missing.");
  }
  return {
    grants: source.grants.map(parseGrant),
    pendingGrants: source.pending_grants.map(parseGrant),
    user: {
      email: text(user.email, "User email"),
      id: text(user.id, "User id"),
      status: userStatus(user.status),
    },
  };
}

function parsePermissionRow(value: unknown) {
  const source = record(value, "Permission row");
  const access = text(source.access, "Permission access");
  if (access !== "allowed" && access !== "denied") {
    throw new Error("Permission access is invalid.");
  }
  return {
    access,
    code: text(source.code, "Permission code"),
    explanation: text(source.explanation, "Permission explanation"),
    id: text(source.id, "Permission row id"),
    source: text(source.source, "Permission source"),
  };
}

type Grant = ReturnType<typeof parseGrant>;
type PermissionRow = ReturnType<typeof parsePermissionRow>;

const columnHelper = createColumnHelper<typeof GRID_FEATURES, PermissionRow>();
const permissionColumns = columnHelper.columns([
  columnHelper.accessor("code", {
    header: "Permission",
    meta: { label: "Permission" },
  }),
  columnHelper.accessor("access", {
    cell: ({ getValue }) => getValue() === "allowed" ? "Allowed" : "Denied",
    header: "Access",
    meta: { label: "Access" },
  }),
  columnHelper.accessor("source", {
    header: "Source",
    meta: { label: "Source" },
  }),
  columnHelper.accessor("explanation", {
    header: "Explanation",
    meta: { label: "Explanation" },
  }),
]);

function csrfToken(): string | null {
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("flo_csrf="));
  return cookie === undefined ? null : decodeURIComponent(cookie.slice("flo_csrf=".length));
}

async function responseCause(response: Response): Promise<string> {
  try {
    const problem = record(await response.json(), "Problem response");
    return typeof problem.detail === "string"
      ? problem.detail
      : `The server responded with ${response.status}.`;
  } catch {
    return `The server responded with ${response.status}.`;
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

type GrantListProps = {
  grants: readonly Grant[];
  heading: string;
  onRevoke: (grant: Grant) => void;
  pending?: boolean;
};

function GrantList({ grants, heading, onRevoke, pending = false }: GrantListProps) {
  const headingId = `access-${pending ? "pending" : "active"}-grants`;
  return (
    <Surface as="section" aria-labelledby={headingId} className="access-grants" shadow="sm">
      <div className="access-section-heading">
        <h2 id={headingId}>{heading}</h2>
        <span>{grants.length.toLocaleString()}</span>
      </div>
      {grants.length === 0 ? (
        <p className="access-section-empty">
          {pending ? "No grants are scheduled." : "No grants are currently active."}
        </p>
      ) : (
        <ul>
          {grants.map((grant) => (
            <li key={grant.id}>
              <div>
                <strong>{grant.roleName}</strong>
                <span>{grant.role}</span>
              </div>
              <dl>
                <div>
                  <dt>Scope</dt>
                  <dd>{grant.scopeName}</dd>
                </div>
                <div>
                  <dt>Granted by</dt>
                  <dd>{grant.grantedBy ?? "Recorded before provenance tracking"}</dd>
                </div>
                <div>
                  <dt>{pending ? "Effective" : "Granted"}</dt>
                  <dd>{formatDate((pending ? grant.effectiveFrom : grant.grantedAt) ?? grant.grantedAt)}</dd>
                </div>
              </dl>
              <button
                aria-label={`Revoke ${grant.roleName} at ${grant.scopeName}`}
                onClick={() => onRevoke(grant)}
                type="button"
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </Surface>
  );
}

export default function EffectiveAccessExplorer({ userId }: { userId: string }) {
  const [access, setAccess] = useState<ReturnType<typeof parseAccess> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);
  const endpoint = `/api/v1/admin/users/${encodeURIComponent(userId)}/effective-access`;

  useEffect(() => {
    const controller = new AbortController();
    fetch(endpoint, { headers: { Accept: "application/json" }, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseCause(response));
        return parseAccess(await response.json());
      })
      .then(setAccess)
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(reason instanceof Error ? reason.message : "The request failed unexpectedly.");
      });
    return () => controller.abort();
  }, [endpoint, requestVersion]);

  const runCommand = useCallback(async (
    path: string,
    method: "DELETE" | "POST",
    success: string,
  ) => {
    setActionError(null);
    const token = csrfToken();
    const headers: Record<string, string> = { Accept: "application/json" };
    if (token !== null) headers["X-CSRF-Token"] = token;
    if (method === "POST") headers["Idempotency-Key"] = crypto.randomUUID();
    const response = await fetch(path, { headers, method });
    if (!response.ok) {
      const cause = response.headers.get("WWW-Authenticate") === "step-up"
        ? "Recent authentication is required before this change."
        : await responseCause(response);
      setActionError(cause);
      return;
    }
    setAnnouncement(success);
    setRequestVersion((current) => current + 1);
  }, []);

  const openPermission = useCallback((row: PermissionRow) => {
    setAnnouncement(`${row.code}: ${row.explanation}`);
  }, []);

  const permissionEndpoint = useMemo(
    () => `${endpoint}/permissions`,
    [endpoint],
  );

  if (loadError !== null) {
    return (
      <Surface as="section" aria-labelledby="access-error-title" className="access-load-state" shadow="sm">
        <ShieldCheck aria-hidden="true" weight="regular" />
        <div role="alert">
          <h2 id="access-error-title">Effective access could not be loaded</h2>
          <p>Cause: {loadError}</p>
          <p>Your place in Administration is preserved. Retry after access or service availability is restored.</p>
          <button
            onClick={() => {
              setAccess(null);
              setLoadError(null);
              setRequestVersion((current) => current + 1);
            }}
            type="button"
          >
            Retry
          </button>
        </div>
      </Surface>
    );
  }

  if (access === null) {
    return (
      <Surface aria-busy="true" aria-label="Loading effective access" className="access-loading" shadow="sm">
        <span />
        <span />
        <span />
      </Surface>
    );
  }

  return (
    <div className="access-explorer">
      <Surface as="section" aria-labelledby="access-user-email" className="access-user-summary" shadow="sm">
        <div>
          <p>User</p>
          <h2 id="access-user-email">{access.user.email}</h2>
          <span className="access-user-id">{access.user.id}</span>
        </div>
        <StatusPill docType="user" status={access.user.status} />
        <button
          disabled={access.user.status === "deactivated"}
          onClick={() => runCommand(`${endpoint.replace("/effective-access", "")}/deactivate`, "POST", `${access.user.email} was deactivated.`)}
          type="button"
        >
          <UserMinus aria-hidden="true" weight="regular" />
          Deactivate user
        </button>
      </Surface>

      {actionError === null ? undefined : (
        <Surface as="section" className="access-action-error" shadow="sm">
          <div role="alert">
            <h2>Change was not applied</h2>
            <p>Cause: {actionError}</p>
            <p>No access data was changed. Re-authenticate, then retry the same action.</p>
          </div>
        </Surface>
      )}

      <div className="access-grant-columns">
        <GrantList
          grants={access.grants}
          heading="Active grants"
          onRevoke={(grant) => runCommand(
            `${endpoint.replace("/effective-access", "")}/roles/${encodeURIComponent(grant.id)}`,
            "DELETE",
            `${grant.roleName} was revoked.`,
          )}
        />
        <GrantList
          grants={access.pendingGrants}
          heading="Pending grants"
          onRevoke={(grant) => runCommand(
            `${endpoint.replace("/effective-access", "")}/roles/${encodeURIComponent(grant.id)}`,
            "DELETE",
            `${grant.roleName} was revoked.`,
          )}
          pending
        />
      </div>

      <section aria-labelledby="permission-table-heading" className="access-permissions">
        <h2 id="permission-table-heading">Permission explanation</h2>
        <p>Search a permission code, role, scope, or denial reason. Every allowed row names its source.</p>
        <DataGrid
          ariaLabel="Effective permissions data grid"
          columns={permissionColumns}
          emptyActionLabel="Refresh permissions"
          emptyMessage="No permission rows are available."
          endpoint={permissionEndpoint}
          filterLabel="Search permissions"
          filterPlaceholder="Permission, role, scope, or reason"
          getRowId={(row) => row.id}
          gridId={`effective-access-${userId}`}
          onEmptyAction={() => setRequestVersion((current) => current + 1)}
          onOpenRow={openPermission}
          parseRow={parsePermissionRow}
          recordLabel="permission rows"
          sortableColumnIds={["code", "access", "source"]}
          userId={`access-explorer-${userId}`}
        />
      </section>
      <p aria-live="polite" className="visually-hidden">{announcement}</p>
    </div>
  );
}
