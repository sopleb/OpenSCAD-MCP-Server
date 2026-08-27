# Client-Supplied Reasoning

<metadata>
  tags: [mcp, architecture-decision, ai, provider-independence]
  supersedes: ai-driven-code-generation.md
</metadata>

## Decision Context

The server previously owned its AI vendor relationships. It called Google Gemini
and Venice.ai for image generation, and it carried a regex engine named
`AIService` that stood in for a language model when translating descriptions
into OpenSCAD.

Both halves failed in different ways. The API clients needed keys, tied the
project to two vendors, and made the server useless to anyone without accounts.
The regex engine matched keywords against a fixed shape table and fell back to a
10mm cube whenever it recognised nothing, which meant a request it could not
parse produced a wrong answer rather than an error.

Meanwhile every caller of this server already has a capable model attached. A
second, weaker model inside the server duplicates that and contradicts it.

## Decision

The server supplies capability. The connected client supplies reasoning.

No API key, no provider SDK, no model of any kind runs in this process. Requests
that need a model reach the caller by one of three routes:

1. **Caller-authored input.** Tools accept structured arguments or complete SCAD
   source. Interpreting a description is the client model's ordinary work.
2. **Registered MCP prompts.** The server renders prompt text; the client's model
   runs it.
3. **Deferred generation.** A tool that needs generated content mid-execution
   returns a `needs_model_input` envelope and resumes through
   `submit_model_input`.

## Options Rejected

**MCP sampling (`sampling/createMessage`).** The obvious mechanism, and unusable.
The 2026-07-28 spec deprecated it under SEP-2577, and Claude Code does not
implement it as a client, so the feature would fail against the most common
caller. The deeper problem is structural: sampling is a server-initiated
request, and a transport without a back-channel cannot carry one. The same
limitation constrains elicitation, which is why `approve_images` degrades to
handing its decision back to the caller. All three chosen routes travel on the
tool call the client already made.

**Keeping the providers behind optional config.** This leaves the dependency in
the tree and the keys in the docs, and it makes behaviour depend on which
account the operator holds. The point of the change is that pointing a different
model at the server changes nothing.

**Server-side image generation in any form.** An MCP server cannot ask Claude or
Grok for an image; neither can return one. Independently, generated views do not
reconstruct: multi-view stereo triangulates between real camera positions, and
several renderings of "the same object" share no scene geometry. Reconstruction
now takes photographs the user supplies.

## Consequences

- The server runs with no configuration. `OPENSCAD_PATH` and `CUDA_MVS_PATH`
  point at binaries, not services.
- Model quality tracks whatever client is connected, and improves with it.
- Errors return as structured status values the calling model can act on. A
  compile error carries the OpenSCAD diagnostics so the caller fixes the line
  and retries, replacing the silent cube fallback.
- Shape parameters validate against signatures parsed from `basic_shapes.scad`,
  so a wrong name produces the list of right ones.
- `tests/test_no_external_ai.py` greps the source for provider hosts and API
  keys. Reintroducing one fails the suite.
