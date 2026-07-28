# Plugin API (SDK)

**Status: Planned (Phase 6.5).** Built after multi-agent orchestration —
the complexity isn't worth it until the core is stable. This document is
the one external contributors will read; keep it accurate as the SDK lands.

## Why the trust boundary exists

A plugin is third-party code running inside an agent that holds real tools.
The failure mode that matters: a plugin manifest simply *claims*
`hard_gate: false` for a capability that shouldn't have it, and the
framework trusts the claim. This is not hypothetical — it is the checkpoint
whose removal from a comparable open-source framework (HexStrike-AI) by
third-party forks turned it into a tool used to exploit disclosed CVEs in
production systems within hours of disclosure.

Therefore: **the kernel classifies capability, never the plugin.** The hard
gate is architecturally non-optional and cannot be eroded by a plugin.

## Manifest schema (planned)

```yaml
name: my-plugin
version: 0.1.0
plugin_api_version: 1          # pinned; incompatible = refused, not guessed
entry_point: my_plugin.main:register
declared_tools:
  - name: port_probe
    description: ...
    parameters: {...}
    hard_gate: false           # a *request* — the kernel may override upward
declared_capabilities:
  - filesystem.read
  - network.raw                # deny-listed -> forces hard_gate: true
```

## Kernel-side capability enforcement (planned, spec §1.1)

1. **Deny-list override.** Capabilities in the fixed deny-list —
   `network.raw`, `subprocess.exec.remote`, `credential.use` — force every
   associated tool to `hard_gate: true` at load time, regardless of the
   manifest's request. There is no "trusted plugin" escape.
2. **Static source scan.** Before registration, the loader scans the plugin
   source for imports/usage implying *undeclared* capabilities (networking
   primitives, subprocess with network-reachable arguments) and classifies
   accordingly. Under-declaring doesn't help.
3. **Restricted loading.** Plugins load in a subprocess with reduced
   privileges (or at minimum a restricted import namespace), so plugin code
   cannot monkeypatch the tool registry or the permission module to unset
   its own gate.
4. **Audit.** Every load emits `plugin.loaded` (see `event_schema.md`)
   listing the capabilities the kernel *actually* assigned and which tools
   were upgraded from the manifest's claim.
5. **Versioning.** The manifest pins `plugin_api_version`; the kernel
   refuses incompatible versions rather than guessing.

## What a plugin can touch

Only kernel-exposed interfaces: the tool registry (register, not mutate),
the event bus (publish/consume with its own consumer name), and
kernel-approved services. Never kernel internals — the same service
boundary rule (see `architecture.md`) that applies to built-in services
applies doubly to plugins.

## Phase 6.5 acceptance test (spec §10)

Load a plugin whose manifest under-declares its capabilities (claims
`hard_gate: false`, source uses raw networking). The kernel must force
`hard_gate: true`, refuse the undeclared path, and emit `plugin.loaded`
recording the override. The gated tool then behaves exactly like any other
`hard_gate: true` tool: no execution without an explicit human approval.
